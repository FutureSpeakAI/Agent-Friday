"""'I need my machine' actually releases the machine.

The button in Settings > Models must do something, not answer with an alert
that nothing is enforced. The rule (headroom.md D1):

    "I need my machine" releases the machine to the user. Local models are
    unloaded from the GPU (Laya is CPU, so it stays), every background and
    scheduled job is paused, and the header shows "Friday is stood down --
    Resume". Interactive chat still works on a cloud seat with its usual visible
    model label, or says it is waiting if cloud is off. The state lasts until
    Resume, or auto-resumes after N hours of the user's choosing. Background
    jobs never wake the GPU while stood down.

The two properties that matter most, and are easiest to get wrong:

  * the state must SURVIVE A RESTART. A stand-down that forgets itself when the
    tray restarts hands the card straight back, which is the opposite of what was
    asked for.
  * a paused job must SKIP VISIBLY. A scheduler that silently drops runs is the
    invisible-success defect this codebase keeps rediscovering.
"""

import time

import pytest


@pytest.fixture
def sd(tmp_path, monkeypatch):
    import agent_friday.core as core
    monkeypatch.setattr(core, "FRIDAY_DIR", tmp_path)
    from agent_friday.services import stand_down as mod
    mod._invalidate()
    # Never touch the real GPU from a test.
    calls = []
    monkeypatch.setattr(mod, "_release_gpu", lambda: calls.append("released"))
    mod._TEST_CALLS = calls
    yield mod
    mod._invalidate()


# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

def test_the_default_state_is_working(sd):
    assert sd.is_stood_down() is False
    st = sd.state()
    assert st["active"] is False


def test_standing_down_releases_the_gpu_and_sets_the_state(sd):
    out = sd.stand_down(requested_by="owner")
    assert out["active"] is True
    assert sd.is_stood_down() is True
    assert "released" in sd._TEST_CALLS, "the GPU was not released"
    assert out["requested_by"] == "owner"
    assert out["since"] > 0


def test_resume_gives_the_machine_back(sd):
    sd.stand_down(requested_by="owner")
    out = sd.resume(requested_by="owner")
    assert out["active"] is False
    assert sd.is_stood_down() is False


def test_the_state_survives_a_restart(sd):
    """The property a stand-down is worthless without."""
    sd.stand_down(requested_by="owner")
    sd._invalidate()                     # every in-process cache cold
    assert sd.is_stood_down() is True, (
        "the stand-down forgot itself; a tray restart would hand the card back")


def test_auto_resume_expires_on_its_own(sd):
    sd.stand_down(requested_by="owner", hours=2)
    st = sd.state()
    assert st["auto_resume_at"] > time.time()

    # wind the clock past the window
    sd._write({"active": True, "since": time.time() - 7200 - 60,
               "requested_by": "owner",
               "auto_resume_at": time.time() - 60})
    sd._invalidate()
    assert sd.is_stood_down() is False, "the auto-resume window did not expire"


def test_no_auto_resume_means_it_waits_for_resume(sd):
    sd.stand_down(requested_by="owner", hours=None)
    assert sd.state()["auto_resume_at"] in (None, 0)
    sd._invalidate()
    assert sd.is_stood_down() is True


# ─────────────────────────────────────────────────────────────────────────────
# What it actually gates
# ─────────────────────────────────────────────────────────────────────────────

def test_a_scheduled_job_is_refused_while_stood_down(sd, monkeypatch):
    from agent_friday.services import scheduler

    ran = []
    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "unit_test_job",
                        {"fn": lambda: ran.append("ran"), "label": "Unit test job"})
    rec = {"task": {"kind": "builtin", "ref": "unit_test_job"}, "id": "s-1"}

    sd.stand_down(requested_by="owner")
    with pytest.raises(scheduler.StoodDown):
        scheduler._run_task(rec)
    assert not ran, "a scheduled job ran while the machine was stood down"


def test_the_same_job_runs_once_resumed(sd, monkeypatch):
    from agent_friday.services import scheduler

    ran = []
    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "unit_test_job2",
                        {"fn": lambda: ran.append("ran"), "label": "Unit test job"})
    rec = {"task": {"kind": "builtin", "ref": "unit_test_job2"}, "id": "s-2"}

    sd.stand_down(requested_by="owner")
    sd.resume(requested_by="owner")
    scheduler._run_task(rec)
    assert ran == ["ran"], "the job did not come back after Resume"


def test_the_refusal_names_the_reason(sd, monkeypatch):
    """A skipped run must say why, not vanish."""
    from agent_friday.services import scheduler
    monkeypatch.setitem(scheduler.BUILTIN_TASKS, "unit_test_job3",
                        {"fn": lambda: None, "label": "Unit test job"})
    sd.stand_down(requested_by="owner")
    with pytest.raises(scheduler.StoodDown) as exc:
        scheduler._run_task({"task": {"kind": "builtin", "ref": "unit_test_job3"},
                             "id": "s-3"})
    msg = str(exc.value).lower()
    assert "stood down" in msg or "machine" in msg


def test_interactive_chat_is_not_blocked(sd):
    """Standing down releases the GPU and pauses BACKGROUND work. It does not
    stop the user talking to Friday -- that would be a worse product than the
    placebo it replaces."""
    sd.stand_down(requested_by="owner")
    assert sd.blocks_interactive_chat() is False


def test_background_work_must_not_wake_the_gpu(sd):
    """The explicit requirement: 'Background jobs never wake the GPU while stood
    down.' A local seat request from background work is refused."""
    sd.stand_down(requested_by="owner")
    assert sd.may_use_local_gpu(interactive=False) is False
    assert sd.may_use_local_gpu(interactive=True) is False, (
        "the machine was released to the user; interactive work must not "
        "quietly reload the card either")


# ─────────────────────────────────────────────────────────────────────────────
# The route is no longer a placebo
# ─────────────────────────────────────────────────────────────────────────────

def test_the_endpoint_reports_enforcement(client, sd):
    r = client.post("/api/machine/level", json={"level": "yield"})
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("enforced") is True, (
        "the route still reports enforced=false; it was a placebo")
    assert "nothing enforces" not in (body.get("message") or "").lower()


def test_the_endpoint_can_resume(client, sd):
    client.post("/api/machine/level", json={"level": "yield"})
    r = client.post("/api/machine/level", json={"level": "working"})
    assert r.status_code == 200
    assert r.get_json().get("active") is False


def test_no_raw_browser_alert_survives_in_the_ui():
    """Every alert() is replaced by Friday's own toast/dialog. A raw browser
    alert is a modal the app cannot style, cannot queue and cannot dismiss for
    you -- and it was how a dead button confessed to being dead."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    src = (root / "index.html").read_text(encoding="utf-8", errors="replace")
    import re
    hits = re.findall(r"(?<![\w.])alert\s*\(", src)
    assert not hits, "%d raw alert() call(s) remain in index.html" % len(hits)
