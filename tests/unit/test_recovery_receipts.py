"""Each chaos kill leaves one recovery receipt and no repeated effect (BD-16; NS-32.10-1,
NS-32.11-1).

A separate interpreter is killed with os._exit in the middle of the work (as in R1,
test_chaos_outward_actions), and this process starts up from the same files. R1 proves the
work is not repeated; these tests prove each interruption also leaves exactly one signed
recovery receipt saying how Friday stopped, the last verified boundary, what was in flight,
whether it may have happened, and what Friday does next. Synthetic data; no network.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from agent_friday.services import approvals
from agent_friday.services import card_recovery
from agent_friday.services import task_journal as tj

SRC = Path(__file__).resolve().parents[2] / "src"


def _kill_after(script: str, tmp_path, timeout=120):
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
    assert proc.returncode == 9, f"child did not die as planned\n{proc.stderr[-2000:]}"
    return proc.stdout.strip().splitlines()


@pytest.fixture
def restart(tmp_path, monkeypatch):
    monkeypatch.setattr(tj, "BASE_DIR_OVERRIDE", tmp_path / "tasks")
    monkeypatch.setattr(approvals, "APPROVALS_FILE", tmp_path / "approvals.json")
    tj.reset_for_tests()
    try:
        from agent_friday.services import recovery_receipts
        recovery_receipts._reset_for_tests()
    except ImportError:
        pass
    import agent_friday.notifications_engine as ne
    monkeypatch.setattr(ne, "push", lambda **kw: kw)


def _recovery(subject_prefix=""):
    from tests import ledger_support
    return [d for d in ledger_support.decisions()
            if d.get("tool") == "recovery" and str(d.get("subject", "")).startswith(subject_prefix)]


_START_TASK = """
    tid = "chaos-" + str(int(time.time() * 1000))
    now = time.time()
    tj.index_put(tid, "Send the invoice", "running", now)
    tj.write_state(tid, {"task_id": tid, "name": "Send the invoice", "status": "running", "created": now})
    print(tid, flush=True)
"""


def test_a_kill_during_a_first_send_leaves_one_receipt_and_no_repeat(tmp_path, restart):
    tid = _kill_after(_START_TASK + """
    task_resume.mark_tool_pending(tid, "send_email", "tu_1")
    """, tmp_path)[-1]
    before = len(_recovery())
    tj.reconcile_on_boot()
    tj.reconcile_on_boot()                                   # a second start tells nothing new
    mine = [d for d in _recovery()[before:] if d["subject"] == "task:" + tid]
    assert len(mine) == 1, "an interruption needs exactly one recovery receipt"
    r = mine[0]
    assert r["may_have_acted"] is True and r["repeated"] is False
    assert r["in_flight"] == "send_email" and r["next"] == "not_repeated_owner_told"
    assert r["exit"] == "unexpected" and r["decision"] == "recovered"


def test_a_kill_during_a_later_send_says_it_will_ask_first(tmp_path, restart):
    tid = _kill_after(_START_TASK + """
    convo = [{"role": "user", "content": "send the invoice"},
             {"role": "assistant", "content": [{"type": "text", "text": "Reading it first."}]}]
    assert task_resume.checkpoint(tid, convo=convo, iteration=1, model="m")
    task_resume.mark_tool_pending(tid, "send_email", "tu_2")
    """, tmp_path)[-1]
    before = len(_recovery())
    tj.reconcile_on_boot()
    mine = [d for d in _recovery()[before:] if d["subject"] == "task:" + tid]
    assert len(mine) == 1, "an interruption needs exactly one recovery receipt"
    r = mine[0]
    assert r["next"] == "asks_the_owner_before_resuming" and r["last_boundary"]
    assert r["may_have_acted"] is True, "a later send in flight may have happened"


def test_an_approved_action_killed_mid_run_leaves_one_receipt(tmp_path, restart):
    aid = _kill_after("""
    rec = approvals.create_approval(kind="tool_confirm", subject_type="tool", subject_id="send_email",
                                    title="Send the invoice to the client", description="synthetic",
                                    payload={"tool": "send_email", "input": {"to": "a@example.invalid"}})
    approvals._patch(rec["approval_id"], status="approved", decided_at=time.time())
    assert approvals.claim_for_execution(rec["approval_id"], by="approval_executor")
    print(rec["approval_id"], flush=True)
    """, tmp_path)[-1]
    before = len(_recovery())
    assert card_recovery.reconcile_at_boot(started_at=time.time() + 5, since=0) == [aid]
    assert card_recovery.reconcile_at_boot(started_at=time.time() + 5, since=0) == []
    mine = [d for d in _recovery()[before:] if d["subject"] == "card:" + aid]
    assert len(mine) == 1
    assert mine[0]["outcome"] == "unknown" and mine[0]["in_flight"] == "send_email"
    assert approvals.claim_for_execution(aid) is False, "an action that may have happened ran again"


def test_a_receipt_that_cannot_be_written_never_stops_a_start(tmp_path, restart, monkeypatch):
    tid = _kill_after(_START_TASK + """
    task_resume.mark_tool_pending(tid, "read_file", "tu_1")
    """, tmp_path)[-1]
    from agent_friday.governance import action_gate
    real = action_gate._receipt

    def no_recovery(entry):
        if entry.get("tool") == "recovery":
            raise OSError("ledger unwritable")
        return real(entry)
    monkeypatch.setattr(action_gate, "_receipt", no_recovery)
    out = tj.reconcile_on_boot()
    assert [s["task_id"] for s in out["interrupted"]] == [tid]


def test_a_stop_after_the_receipt_but_before_the_mark_does_not_write_a_second(tmp_path, restart,
                                                                               monkeypatch):
    tid = _kill_after(_START_TASK + """
    task_resume.mark_tool_pending(tid, "send_email", "tu_1")
    """, tmp_path)[-1]
    before = len(_recovery())
    real_write = tj.write_state

    def die_here(task_id, st, *a, **k):
        raise SystemExit("killed between the receipt and the mark")
    monkeypatch.setattr(tj, "write_state", die_here)
    with pytest.raises(SystemExit):
        tj.reconcile_on_boot()                               # receipt written, mark not
    monkeypatch.setattr(tj, "write_state", real_write)
    from agent_friday.services import recovery_receipts
    recovery_receipts._reset_for_tests()                     # a new process: read from the ledger
    tj.reconcile_on_boot()
    mine = [d for d in _recovery()[before:] if d["subject"] == "task:" + tid]
    assert len(mine) == 1, "the next start wrote a second receipt for the same interruption"

