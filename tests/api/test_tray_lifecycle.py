"""Finished cards leave the tray; resume handles do not.

Finished and interrupted task cards must not accumulate forever: each can be
dismissed by hand, ages out after a set time, and an interrupted card offers a
resume button. The notification QUEUE is capped separately; this is about the
tray's TASKS section, where a terminal record otherwise never leaves and the ✕
on a running card means *cancel*, not dismiss.

A single `max_age_hours` sweep is the dangerous version. Interrupted tasks can
hold resume checkpoints — the handles on unfinished work that the checkpoint
machinery exists to offer. Expiring those on the same clock as a receipt would
quietly delete exactly what is being preserved. So the lifetimes are
per-class, and a resumable card has no clock at all.
"""

import pathlib
import time

import pytest

import agent_friday.services.agent as ag
from agent_friday.services import tray_lifecycle as tl

ROOT = pathlib.Path(__file__).resolve().parents[2]
HTML_FILES = ("index.html", "ui_parts/app.html")
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    with ag.TASKS_LOCK:
        saved = dict(ag.TASKS)
        ag.TASKS.clear()
    monkeypatch.setattr(tl, "_settings", lambda: {})
    monkeypatch.setattr(tl, "_has_checkpoint", lambda tid: False)
    yield
    with ag.TASKS_LOCK:
        ag.TASKS.clear()
        ag.TASKS.update(saved)


def _row(status="complete", ended_hours_ago=0.0, tid="t-1", **extra):
    r = {"task_id": tid, "id": tid, "name": "a task", "status": status,
         "created": NOW - 7200, "ended": NOW - ended_hours_ago * 3600}
    r.update(extra)
    return r


# ── what stays ───────────────────────────────────────────────────────────────

def test_a_running_task_never_expires_however_old():
    """The fifteen-minute chat release in another costume. Age is not death."""
    r = _row(status="running", ended_hours_ago=0)
    r["ended"] = None
    assert tl.visible(r, now=NOW + 86400 * 30) is True


def test_a_queued_task_stays():
    assert tl.visible(_row(status="queued-for-seat"), now=NOW) is True


def test_a_fresh_receipt_stays_then_ages_out():
    assert tl.visible(_row("complete", 1), now=NOW) is True
    assert tl.visible(_row("complete", 9), now=NOW) is False


def test_a_failure_is_kept_much_longer_than_a_success():
    """A receipt has nothing to act on once seen. A failure might."""
    assert tl.visible(_row("complete", 9), now=NOW) is False
    assert tl.visible(_row("failed", 9), now=NOW) is True
    assert tl.visible(_row("timeout", 9), now=NOW) is True


def test_an_interrupted_task_holding_a_checkpoint_never_expires(monkeypatch):
    """THE one that must not be swept. It is a handle on unfinished work, and
    a uniform age sweep would throw it away — the exact loss the checkpoint
    work exists to end."""
    monkeypatch.setattr(tl, "_has_checkpoint", lambda tid: True)
    assert tl.visible(_row("interrupted", 24 * 365), now=NOW) is True


def test_an_interrupted_task_with_nothing_saved_does_age_out(monkeypatch):
    monkeypatch.setattr(tl, "_has_checkpoint", lambda tid: False)
    assert tl.visible(_row("interrupted", 1), now=NOW) is True
    assert tl.visible(_row("interrupted", 72), now=NOW) is False


def test_a_broken_visibility_check_shows_the_row(monkeypatch):
    """A bug in this function must never be able to hide work from the user."""
    monkeypatch.setattr(tl, "tray_hours",
                        lambda s: (_ for _ in ()).throw(RuntimeError("x")))
    assert tl.visible(_row("complete", 500), now=NOW) is True


def test_the_lifetimes_are_configurable(monkeypatch):
    monkeypatch.setattr(tl, "_settings",
                        lambda: {"task_tray": {"hours": {"complete": 100}}})
    assert tl.visible(_row("complete", 50), now=NOW) is True
    monkeypatch.setattr(tl, "_settings",
                        lambda: {"task_tray": {"hours": {"complete": None}}})
    assert tl.visible(_row("complete", 10000), now=NOW) is True


# ── dismissing ───────────────────────────────────────────────────────────────

def test_dismiss_hides_the_card_and_keeps_the_record():
    with ag.TASKS_LOCK:
        ag.TASKS["t-1"] = _row("complete", 0, result="the answer")
    assert tl.dismiss("t-1") is True
    with ag.TASKS_LOCK:
        rec = ag.TASKS["t-1"]
    assert rec["tray_dismissed"] is True
    assert rec["result"] == "the answer", "dismiss destroyed the record"
    assert rec["status"] == "complete", "dismiss changed the outcome"
    assert tl.visible(rec, now=NOW) is False


def test_a_running_task_cannot_be_dismissed():
    """Hiding live work would lose sight of something still happening. The
    running card has Cancel; that is the control that means stop."""
    with ag.TASKS_LOCK:
        ag.TASKS["t-run"] = _row("running", 0, tid="t-run")
    assert tl.dismiss("t-run") is False
    with ag.TASKS_LOCK:
        assert "tray_dismissed" not in ag.TASKS["t-run"]


def test_dismiss_all_spares_live_work_and_resume_handles(monkeypatch):
    monkeypatch.setattr(tl, "_has_checkpoint", lambda tid: tid == "t-resume")
    with ag.TASKS_LOCK:
        ag.TASKS.update({
            "t-done": _row("complete", 1, tid="t-done"),
            "t-run": _row("running", 0, tid="t-run"),
            "t-resume": _row("interrupted", 1, tid="t-resume"),
            "t-lost": _row("interrupted", 1, tid="t-lost"),
        })
    n = tl.dismiss_all()
    with ag.TASKS_LOCK:
        snap = {k: bool(v.get("tray_dismissed")) for k, v in ag.TASKS.items()}
    assert snap["t-done"] is True and snap["t-lost"] is True
    assert snap["t-run"] is False, "clear-all hid a running task"
    assert snap["t-resume"] is False, "clear-all discarded a resume handle"
    assert n == 2


def test_dismissing_an_unknown_task_is_false_not_a_crash():
    assert tl.dismiss("nobody") is False


# ── the row carries what the button needs ────────────────────────────────────

def test_an_interrupted_row_is_annotated_with_its_resume_verdict(monkeypatch):
    import agent_friday.services.task_resume as tr
    monkeypatch.setattr(tr, "resumability", lambda tid: {
        "resumable": True, "reason": "9 step(s) of work are saved.",
        "needs_confirmation": False, "iteration": 9})
    row = tl.annotate(_row("interrupted", 1))
    assert row["resumable"] is True
    assert row["resume_from_step"] == 9
    assert "9 step(s)" in row["resume_reason"]
    assert row["resume_needs_confirmation"] is False


def test_a_gated_row_says_so_so_the_button_can_ask(monkeypatch):
    import agent_friday.services.task_resume as tr
    monkeypatch.setattr(tr, "resumability", lambda tid: {
        "resumable": True, "reason": "'send_email' was running ... re-runs it.",
        "needs_confirmation": True, "iteration": 4})
    row = tl.annotate(_row("interrupted", 1))
    assert row["resume_needs_confirmation"] is True


def test_a_finished_row_is_not_annotated(monkeypatch):
    import agent_friday.services.task_resume as tr
    monkeypatch.setattr(tr, "resumability",
                        lambda tid: pytest.fail("asked about a finished task"))
    assert "resumable" not in tl.annotate(_row("complete", 1))


# ── the routes ───────────────────────────────────────────────────────────────

def test_the_dismiss_route_hides_one_card(client):
    with ag.TASKS_LOCK:
        ag.TASKS["t-1"] = _row("complete", 0)
    r = client.post("/api/tasks/t-1/dismiss")
    assert r.status_code == 200 and r.get_json()["dismissed"] is True


def test_the_dismiss_route_refuses_live_work_with_a_reason(client):
    with ag.TASKS_LOCK:
        ag.TASKS["t-run"] = _row("running", 0, tid="t-run")
    r = client.post("/api/tasks/t-run/dismiss")
    assert r.status_code == 409
    assert "cancel it instead" in r.get_json()["error"]


def test_a_dismissed_card_leaves_the_list(client):
    with ag.TASKS_LOCK:
        ag.TASKS["t-1"] = _row("complete", 0)
    assert any(t["task_id"] == "t-1"
               for t in client.get("/api/tasks").get_json()["tasks"])
    client.post("/api/tasks/t-1/dismiss")
    assert not any(t["task_id"] == "t-1"
                   for t in client.get("/api/tasks").get_json()["tasks"])


# ── the controls exist in BOTH html files ────────────────────────────────────

@pytest.mark.parametrize("rel", HTML_FILES)
def test_every_finished_card_has_a_dismiss_control(rel):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    assert "/dismiss'" in text or '/dismiss"' in text, \
        f"{rel}: no per-card dismiss"
    assert "Dismiss this card. The record and its journal are kept." in text, \
        f"{rel}: dismiss does not say that it keeps the record"
    assert "/api/tasks/dismiss-all" in text, f"{rel}: no clear-finished control"


@pytest.mark.parametrize("rel", HTML_FILES)
def test_an_interrupted_card_offers_resume(rel):
    text = (ROOT / rel).read_text(encoding="utf-8", errors="replace")
    assert "/resume'" in text or "/resume\"" in text, f"{rel}: no resume control"
    assert "resume_needs_confirmation" in text, \
        f"{rel}: resume does not ask before re-running an in-flight tool"
    assert "Resume from step " in text, \
        f"{rel}: the button does not say where it would pick up"
    assert "Cannot resume" in text, \
        f"{rel}: an unresumable interrupted task gives no reason"
