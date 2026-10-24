"""BD-25 / NS-33.8-1: heavy background work waits while a GPU is too hot, and says why.

Before: the Library's indexing, the nightly practice trials and the SkillOpt night run waited
on battery but ran on whatever the GPU's temperature was. Now they wait from 85 °C until the
GPU is back under 80 °C, with "GPU too hot (... °C ...)" where the job shows it waits. Where no
temperature can be read nothing is held. The GPU is faked through machine_monitor's one
nvidia-smi call; nothing here touches real hardware.
"""
from __future__ import annotations

import threading

import pytest

from agent_friday.services import machine_monitor as mm

_ROW = "0, Test GPU, 24576, 4096, 20480, 97, 310.5, 350.0, 2520"


@pytest.fixture
def gpu(monkeypatch):
    """Set the fake GPU's temperature: gpu(90), gpu(None) for a card that reports none."""
    state = {"temp": None}

    def run(cmd, timeout=15):
        if "nvidia-smi" not in " ".join(cmd):
            return ""
        t = state["temp"]
        return _ROW + (", %s" % t if t is not None else ", [N/A]") + "\n"
    monkeypatch.setattr(mm, "_run", run)

    def set_temp(t):
        state["temp"] = t
        mm.reset_gpu_cache_for_tests()
    try:
        from agent_friday.services import thermal
        thermal.reset_for_tests()
    except ImportError:
        pass
    set_temp(None)
    yield set_temp
    mm.reset_gpu_cache_for_tests()
    try:
        from agent_friday.services import thermal
        thermal.reset_for_tests()
    except ImportError:
        pass


def test_the_gpu_temperature_is_read_and_a_missing_one_is_none(gpu):
    gpu(91)
    assert mm.gpu_rows()[0].get("temp_c") == 91
    gpu(None)
    assert mm.gpu_rows()[0].get("temp_c") is None, "an unreadable temperature is never 0"


def test_library_indexing_waits_while_the_gpu_is_too_hot(gpu, monkeypatch):
    from agent_friday.services.library import runtime
    monkeypatch.setattr(runtime, "_on_battery", lambda: False)
    gpu(90)
    ok, why = runtime.machine_is_free()
    assert ok is False and "GPU is too hot" in why and "90" in why
    gpu(60)
    assert runtime.machine_is_free() == (True, "")


def test_nothing_waits_when_no_temperature_can_be_read(gpu, monkeypatch):
    from agent_friday.services.library import runtime
    monkeypatch.setattr(runtime, "_on_battery", lambda: False)
    gpu(None)
    assert runtime.machine_is_free() == (True, "")


def test_it_stays_hot_until_it_is_well_under_the_line(gpu, monkeypatch):
    from agent_friday.services.library import runtime
    monkeypatch.setattr(runtime, "_on_battery", lambda: False)
    gpu(84)
    assert runtime.machine_is_free()[0] is True, "under the line: runs"
    gpu(86)
    assert runtime.machine_is_free()[0] is False
    gpu(82)
    assert runtime.machine_is_free()[0] is False, "cooling but not cool: still waits"
    gpu(80)
    assert runtime.machine_is_free()[0] is False, "it waits until it is UNDER 80"
    gpu(79)
    assert runtime.machine_is_free()[0] is True


def test_the_practice_night_run_waits_and_says_why(gpu, monkeypatch):
    from agent_friday.services import skillopt_evaluator, trial_job, trials
    monkeypatch.setattr(trials, "on_ac_power", lambda: True)
    monkeypatch.setattr(trials, "night_seconds_used", lambda: 0.0)
    monkeypatch.setattr(trial_job, "_gate_blocks", lambda: "")
    gpu(95)
    assert "GPU is too hot" in skillopt_evaluator.night_gate()

    class _Watcher:
        def __init__(self):
            self.tripped = threading.Event()
    assert trial_job._stop_now(_Watcher()) is True, "a running trial stops before its next episode"
    gpu(50)
    assert skillopt_evaluator.night_gate() == ""
    assert trial_job._stop_now(_Watcher()) is False


def test_the_trial_run_is_skipped_with_the_reason(gpu, monkeypatch):
    from agent_friday.services import r0_nightly, trial_job, trials
    monkeypatch.setattr(trials, "candidates", lambda status: [{"id": "c1"}] if status == "queued" else [])
    monkeypatch.setattr(r0_nightly, "ensure_scheduled", lambda: None)
    monkeypatch.setattr(r0_nightly, "due", lambda: False)
    monkeypatch.setattr(trials, "on_ac_power", lambda: True)
    monkeypatch.setattr(trials, "night_seconds_used", lambda: 0.0)
    monkeypatch.setattr(trials, "weekly_digest", lambda: None)
    gpu(92)
    out = trial_job.run_pending_trials()
    assert "GPU is too hot" in out.get("skipped", ""), out
    assert out["ran"] == []


def test_lays_refit_waits_and_says_why(gpu, monkeypatch):
    from agent_friday.services import laya_personal_fit as fit
    monkeypatch.setattr(fit.lp, "learning_state", lambda profile=None: {"on": True, "why": ""})
    monkeypatch.setattr(fit.store, "vault_open", lambda: True)
    monkeypatch.setattr(fit, "on_ac_override", lambda: True)
    monkeypatch.setattr(fit, "presence_override", lambda: True)
    gpu(90)
    assert "GPU is too hot" in fit.blocked_reason("default")
