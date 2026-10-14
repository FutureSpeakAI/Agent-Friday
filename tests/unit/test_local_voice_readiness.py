"""LV-7: the stale capability snapshot, fixed.

A machine nobody had measured read "not capable", the owner stayed on cloud voice,
and nothing ever offered to measure or to switch. These tests hold the fix:

  1. unknown ("not measured") stays distinct from refused ("can't"), in the snapshot;
  2. re-assessment triggers (install, measurement, GPU/driver change, weekly)
     refresh the stored snapshot, as a background job through the local scheduler;
  3. the job never runs during a call, and not before the owner is idle;
  4. ONE switch offer, with the measured numbers, both choices, nothing preselected,
     through the notifications engine and the notice registry; declined is final;
     nothing ever switches the engine by itself;
  5. "why cloud voice" says your choice / local not ready / local not measured;
  6. voice parity: the two phrasings reach the tool through the router.

No real measurement, no models: the measurer and the assessor are mocked.
"""
from __future__ import annotations

import contextlib
import inspect
import json
from pathlib import Path

import pytest

from agent_friday.privacy import cloud_consent as cc
from agent_friday.services import local_scheduler as ls
from agent_friday.services import local_voice_readiness as lvr
from agent_friday.services import voice_live_channel as vlc

ROOT = Path(__file__).resolve().parents[2]
_REAL_VISIBLE_JOB = lvr._visible_job


# ── fixtures ─────────────────────────────────────────────────────────────────

def _row(fits, runs_well, modality="stt", explanation=""):
    return {"model_id": "m-" + modality, "modality": modality, "verdicts": {
        "fits": {"status": fits, "explanation": explanation},
        "runs_well": {"status": runs_well, "explanation": explanation},
        "worth_it": {"status": "ready", "explanation": ""}}}


def _assess(monkeypatch, voice_rows, *, image_rows=None, stage=None):
    """assess_local_capability against a synthetic catalogue (no models, no machine)."""
    from agent_friday.routes import intelligence
    from agent_friday.services import local_seats
    ok = [_row("ready", "ready")]
    monkeypatch.setattr(local_seats, "installed", lambda: [])
    monkeypatch.setattr(intelligence, "local_models_catalog", lambda prof, _: {
        "text": ok, "voice": voice_rows,
        "image": image_rows if image_rows is not None else ok, "video": ok})
    chain = {"voice_only": {"stages": [stage] if stage else [{"where": "leased"}], "contract_ok": True}}
    monkeypatch.setattr(intelligence, "build_starter_set", lambda prof: {"chain": chain})
    return cc.assess_local_capability(profile={})


CAPABLE_SNAP = {"capable": True, "state": "capable", "assessed_at": "2026-10-10T00:00:00+00:00",
                "roles": {"voice": {"ok": True, "state": "capable", "why": None}}}
UNKNOWN_SNAP = {"capable": False, "state": "unknown", "assessed_at": "2026-10-10T00:00:00+00:00",
                "roles": {"voice": {"ok": False, "state": "unknown", "why": "not measured on this machine yet"}}}
REFUSED_SNAP = {"capable": False, "state": "refused", "assessed_at": "2026-10-10T00:00:00+00:00",
                "roles": {"voice": {"ok": False, "state": "refused", "why": "the card is too small"}}}
NUMBERS = {"measured": True,
           "stt": {"model_id": "stt-x", "host_ram_mib": 480.0, "load_s": 2.1, "measured_at": "2026-10-10"},
           "tts": {"model_id": "tts-x", "host_ram_mib": 120.0, "load_s": 1.4, "measured_at": "2026-10-10"}}


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    """A scratch state file, a quiet scheduler, an idle owner."""
    monkeypatch.setattr(lvr, "_state_path", lambda: tmp_path / "local_voice_readiness.json")
    monkeypatch.setattr(lvr, "_visible_job", contextlib.nullcontext)       # the real one imports the agent
    monkeypatch.setattr(lvr, "_step", lambda name: None)
    ls.reset_for_tests()
    ls.set_ram_probe(lambda: 64.0)
    monkeypatch.setattr(ls, "owner_idle_s", lambda: 10_000.0)
    lvr.reset_for_tests()
    yield
    ls.reset_for_tests()
    lvr.reset_for_tests()
    for cid in list(vlc._CHANNELS):
        vlc._CHANNELS.pop(cid, None)


class Store:
    """A settings store: what `_load_settings` reads and `_save_settings` writes."""

    def __init__(self, monkeypatch, **initial):
        self.data = dict({"voice_engine": "gemini", lvr.OFFER_KEY: ""}, **initial)
        self.writes = []
        monkeypatch.setattr(lvr, "_settings", lambda: dict(self.data))
        import agent_friday.core as core
        monkeypatch.setattr(core, "_save_settings", self._save)

    def _save(self, patch, **kw):
        self.writes.append(dict(patch))
        self.data.update(patch)
        return dict(self.data)


class Measurer:
    def __init__(self, monkeypatch):
        self.calls = 0
        self.class_seen = []
        monkeypatch.setattr(lvr, "_measure_voice", self._measure)

    def _measure(self):
        self.calls += 1
        self.class_seen.append(ls._CLASS.get())
        return {"stt": {"status": "measured"}, "tts": {"status": "measured"}}


class Assessor:
    """cloud_consent.refresh_snapshot, mocked: records the call, returns a snapshot."""

    def __init__(self, monkeypatch, snap=None):
        self.snap = snap if snap is not None else CAPABLE_SNAP
        self.calls = 0
        monkeypatch.setattr(cc, "refresh_snapshot", self._refresh)
        monkeypatch.setattr(cc, "stored_snapshot", lambda: self.snap)

    def _refresh(self, profile=None, snapshot=None):
        self.calls += 1
        return self.snap


class Admits:
    """local_scheduler.admit, recorded: the class it was asked at, and whether it was released."""

    def __init__(self, monkeypatch, during=None):
        self.calls = []
        self.released = 0
        self.during = during
        monkeypatch.setattr(ls, "admit", self._admit)

    def _admit(self, kind, **kw):
        self.calls.append(dict(kw, kind=kind))
        if self.during:
            self.during()
        outer = self

        class _G:
            def release(self_inner):
                outer.released += 1
        return _G()


def _open_call():
    vlc.register("conv-1", lambda text, kind: None)


# ── 1. unknown is not refused ────────────────────────────────────────────────

def test_an_unmeasured_voice_reads_unknown_never_refused(monkeypatch):
    snap = _assess(monkeypatch, [_row("unknown", "unknown", "stt"), _row("unknown", "unknown", "tts")])
    assert snap["roles"]["voice"]["state"] == "unknown"
    assert snap["roles"]["voice"]["ok"] is False
    assert snap["state"] == "unknown" and snap["capable"] is False
    assert "measured" in snap["roles"]["voice"]["why"]
    assert snap["assessed_at"]


def test_a_measured_no_reads_refused(monkeypatch):
    snap = _assess(monkeypatch, [_row("ready", "degraded", "stt", "not enough memory"), _row("ready", "ready", "tts")])
    assert snap["roles"]["voice"]["state"] == "refused"
    assert snap["state"] == "refused"
    assert "memory" in snap["roles"]["voice"]["why"]


def test_one_unmeasured_half_and_one_refused_half_is_a_firm_no(monkeypatch):
    snap = _assess(monkeypatch, [_row("refused", "unknown", "stt", "will not fit"), _row("unknown", "unknown", "tts")])
    assert snap["roles"]["voice"]["state"] == "refused"


def test_a_ready_voice_is_capable_and_the_whole_machine_can_be_too(monkeypatch):
    snap = _assess(monkeypatch, [_row("ready", "ready", "stt"), _row("ready", "ready", "tts")])
    assert snap["roles"]["voice"]["state"] == "capable"
    assert snap["state"] == "capable" and snap["capable"] is True


def test_the_whole_machine_is_refused_when_any_role_is_refused_even_if_voice_is_unknown(monkeypatch):
    snap = _assess(monkeypatch, [_row("unknown", "unknown", "stt"), _row("unknown", "unknown", "tts")],
                   image_rows=[_row("refused", "ready", "image", "needs a bigger card")])
    assert snap["roles"]["voice"]["state"] == "unknown"
    assert snap["state"] == "refused"


def test_a_chain_that_fails_while_a_role_is_unmeasured_is_not_a_firm_no(monkeypatch):
    snap = _assess(monkeypatch, [_row("unknown", "unknown", "stt"), _row("ready", "ready", "tts")],
                   stage={"role": "stt", "where": "cloud"})
    assert snap["chain_ok"] is False
    assert snap["chain_state"] == "unknown"


def test_a_crashed_assessment_is_unknown_not_incapable(monkeypatch):
    from agent_friday.services import hardware_profile as hwp
    monkeypatch.setattr(hwp, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("probe died")))
    snap = cc.assess_local_capability()
    assert snap["state"] == "unknown" and snap["capable"] is False


def test_a_snapshot_written_before_the_states_reads_unknown_until_refreshed():
    assert cc.snapshot_state({"capable": False, "roles": {}}) == "unknown"     # the owner's case
    assert cc.snapshot_is_legacy({"capable": False, "roles": {}}) is True
    assert cc.snapshot_state({"capable": True}) == "capable"
    assert cc.snapshot_state(None) == "unknown" and cc.snapshot_is_legacy(None) is True
    assert cc.snapshot_state(REFUSED_SNAP) == "refused"
    assert cc.snapshot_is_legacy(REFUSED_SNAP) is False


def test_record_consent_says_to_measure_when_the_answer_is_unknown(monkeypatch):
    monkeypatch.setattr(cc, "assess_local_capability", lambda profile=None: UNKNOWN_SNAP)
    with pytest.raises(cc.ConsentRejected) as e:
        cc.record_consent(cc.CHOICE_LOCAL)
    assert "not been measured" in str(e.value) and "Measure" in str(e.value)


def test_record_consent_still_refuses_a_measured_no_with_the_old_reason(monkeypatch):
    monkeypatch.setattr(cc, "assess_local_capability", lambda profile=None: REFUSED_SNAP)
    with pytest.raises(cc.ConsentRejected) as e:
        cc.record_consent(cc.CHOICE_LOCAL)
    assert "promise the hardware breaks" in str(e.value)


def test_status_carries_the_snapshot_state(monkeypatch):
    from agent_friday import core
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {"cloud_consent": {
        "answered": True, "choice": cc.CHOICE_CLOUD, "at": "2026-01-01T00:00:00+00:00",
        "capability_snapshot": {"capable": False, "roles": {}}}}})
    assert cc.status()["snapshot_state"] == "unknown"


# ── 2. re-assessment refreshes the stored snapshot ───────────────────────────

def test_refresh_snapshot_stores_the_new_snapshot_and_never_touches_the_answer(monkeypatch):
    from agent_friday import core
    record = {"answered": True, "choice": cc.CHOICE_CLOUD, "at": "2026-01-01T00:00:00+00:00",
              "capability_snapshot": {"capable": False, "roles": {}}}
    monkeypatch.setattr(core, "_load_settings", lambda: {"model_routing": {"cloud_consent": dict(record)}})
    saved = []
    monkeypatch.setattr(core, "_save_settings", lambda data, **kw: saved.append((data, kw)))
    cc.refresh_snapshot(snapshot=CAPABLE_SNAP)
    data, kw = saved[0]
    rec = data["model_routing"]["cloud_consent"]
    assert rec["capability_snapshot"] == CAPABLE_SNAP
    assert (rec["answered"], rec["choice"], rec["at"]) == (True, cc.CHOICE_CLOUD, record["at"])
    assert "unrestricted_cloud" not in data["model_routing"], "a re-assessment is not an answer"
    assert kw.get("_internal_cloud_consent_write") is True


def test_hardware_signature_moves_with_the_gpu_driver_and_ram(monkeypatch):
    base = {"gpus": [{"name": "Card A", "vram_total_mib": 12000, "driver": "551.1"}], "ram": {"total_mib": 32000}}
    same = json.loads(json.dumps(base))
    newer = json.loads(json.dumps(base))
    newer["gpus"][0]["driver"] = "560.2"
    other = json.loads(json.dumps(base))
    other["gpus"][0]["name"] = "Card B"
    ram = json.loads(json.dumps(base))
    ram["ram"]["total_mib"] = 64000
    sig = lvr.hardware_signature
    assert sig(base) == sig(same)
    assert len({sig(base), sig(newer), sig(other), sig(ram)}) == 4


def test_a_gpu_or_driver_change_asks_for_a_reassessment(monkeypatch):
    Assessor(monkeypatch)
    Admits(monkeypatch)
    lvr._save_state({"signature": "old", "assessed_at": 1_000_000.0})
    monkeypatch.setattr(lvr, "hardware_signature", lambda profile=None: "new")
    monkeypatch.setattr(ls, "owner_idle_s", lambda: 0.0)          # busy: it asks, it does not run
    lvr.tick(now=1_000_100.0)
    assert "hardware" in lvr.job_status()["reasons"]


def test_an_unchanged_machine_asks_for_nothing(monkeypatch):
    Assessor(monkeypatch)
    lvr._save_state({"signature": "same", "assessed_at": 1_000_000.0})
    monkeypatch.setattr(lvr, "hardware_signature", lambda profile=None: "same")
    out = lvr.tick(now=1_000_100.0)
    assert out["ran"] is False
    assert lvr.job_status()["wanted"] is False


def test_a_week_gone_by_asks_and_six_days_do_not(monkeypatch):
    Assessor(monkeypatch)
    monkeypatch.setattr(lvr, "hardware_signature", lambda profile=None: "same")
    monkeypatch.setattr(ls, "owner_idle_s", lambda: 0.0)
    lvr._save_state({"signature": "same", "assessed_at": 1_000_000.0})
    lvr.tick(now=1_000_000.0 + 6 * 86400)
    assert lvr.job_status()["wanted"] is False
    lvr.tick(now=1_000_000.0 + 7 * 86400 + 1)
    assert lvr.job_status()["reasons"] == ["weekly"]


def test_a_snapshot_from_before_the_states_is_refreshed_at_the_next_tick(monkeypatch):
    Assessor(monkeypatch, snap={"capable": False, "roles": {}})        # legacy: no "state"
    monkeypatch.setattr(lvr, "hardware_signature", lambda profile=None: "same")
    monkeypatch.setattr(ls, "owner_idle_s", lambda: 0.0)
    lvr.tick(now=1_000_000.0)
    assert lvr.job_status()["reasons"] == ["out_of_date"]


def test_an_install_or_a_measurement_asks_for_a_reassessment():
    lvr.note_event("installed")
    lvr.note_event("measured")
    assert lvr.job_status()["reasons"] == ["installed", "measured"]
    assert lvr.job_status()["wanted"] is True


def test_recording_a_footprint_asks_for_a_reassessment(monkeypatch):
    """The measurement path itself carries the trigger: residency_catalog.record_footprint."""
    from agent_friday.services import residency_catalog as rc
    monkeypatch.setattr(rc, "record_measurement", lambda *a, **k: None)
    rc.record_footprint("some-model", "some|fingerprint", {"modality": "stt", "basis": "measured"})
    assert "measured" in lvr.job_status()["reasons"]


def test_the_install_paths_carry_the_trigger():
    from agent_friday.services import model_download, voice_installer
    assert "local_voice_readiness" in inspect.getsource(model_download.run_job)
    assert "local_voice_readiness" in inspect.getsource(voice_installer._run_job)


def test_the_job_stores_a_fresh_snapshot_and_clears_what_was_wanted(monkeypatch):
    a = Assessor(monkeypatch)
    Admits(monkeypatch)
    monkeypatch.setattr(lvr, "hardware_signature", lambda profile=None: "sig-1")
    lvr.request_reassess("weekly")
    out = lvr.run_pending()
    assert out["ran"] is True and a.calls == 1
    status = lvr.job_status()
    assert status["wanted"] is False and status["state"] == "done"
    assert lvr._load_state()["signature"] == "sig-1"


def test_the_job_does_not_trigger_itself(monkeypatch):
    """Its own measurement writes a footprint, which asks for a re-assessment: ignored while it runs."""
    from agent_friday.services import residency_catalog as rc
    Assessor(monkeypatch)
    Admits(monkeypatch)
    monkeypatch.setattr(rc, "record_measurement", lambda *a, **k: None)

    def measure():
        rc.record_footprint("stt-x", "fp", {"modality": "stt", "basis": "measured"})
        return {}
    monkeypatch.setattr(lvr, "_measure_voice", measure)
    lvr.request_measure("owner")
    assert lvr.run_pending()["ran"] is True
    assert lvr.job_status()["wanted"] is False


def test_the_job_runs_through_the_local_scheduler_as_background(monkeypatch):
    m = Measurer(monkeypatch)
    Assessor(monkeypatch)
    adm = Admits(monkeypatch)
    lvr.request_measure("owner")
    assert lvr.run_pending()["ran"] is True
    assert adm.calls and adm.calls[0]["cls"] == "background"
    assert adm.released == 1, "the slot is given back"
    assert m.class_seen == ["background"], "the measuring ran inside local_scheduler.work(background)"


def test_the_job_is_a_visible_task_with_its_steps(monkeypatch):
    """Every job is visible: the run opens a visible job (workspace Settings, deferrable, not a
    second queue entry) and names its steps, so the Activity lane shows it."""
    seen = {"opened": [], "steps": [], "closed": 0}

    class _VJ:
        def __enter__(self):
            seen["closed"] -= 1
            return self

        def __exit__(self, *exc):
            seen["closed"] += 1
            return False

    def _open():
        seen["opened"].append(True)
        return _VJ()
    monkeypatch.setattr(lvr, "_visible_job", _open)
    monkeypatch.setattr(lvr, "_step", lambda name: seen["steps"].append(name))
    Measurer(monkeypatch)
    Assessor(monkeypatch)
    Admits(monkeypatch)
    lvr.request_measure("owner")
    assert lvr.run_pending()["ran"] is True
    assert seen["opened"] == [True] and seen["closed"] == 0
    assert seen["steps"] == ["Measuring local voice", "Re-assessing what this PC can run"]


def test_the_real_visible_job_is_registered_under_settings(monkeypatch):
    """The seam builds a `visible_job` for the Settings workspace; it is not queued twice
    (the scheduler's admit is the queue entry)."""
    from agent_friday.services import visible_jobs as vj
    got = {}
    monkeypatch.setattr(vj, "visible_job", lambda name, **kw: got.update(name=name, **kw) or "cm")
    monkeypatch.setattr(lvr, "_visible_job", _REAL_VISIBLE_JOB)     # the seam under test, not the stand-in
    assert lvr._visible_job() == "cm"
    assert got["name"] == lvr.JOB_LABEL and got["workspace"] == "settings"
    assert got["queued"] is False and got["deferrable"] is True and got["eager"] is True


def test_the_job_is_listed_in_the_schedulers_status_and_can_be_cancelled(monkeypatch):
    lvr.request_measure("owner")
    jobs = ls.jobs()
    mine = [j for j in jobs if j["id"] == lvr.JOB_ID]
    assert mine and mine[0]["class"] == "background" and mine[0]["state"] == "waiting"
    assert mine[0]["cancellable"] is True
    assert ls.cancel(lvr.JOB_ID)["ok"] is True
    assert lvr.job_status()["wanted"] is False
    assert [j for j in ls.jobs() if j["id"] == lvr.JOB_ID] == []


# ── 3. never during a call, only when idle ───────────────────────────────────

def test_asking_to_measure_measures_nothing(monkeypatch):
    m = Measurer(monkeypatch)
    lvr.request_measure("owner")
    assert m.calls == 0


def test_the_job_waits_while_a_voice_session_is_open(monkeypatch):
    m = Measurer(monkeypatch)
    Assessor(monkeypatch)
    Admits(monkeypatch)
    _open_call()
    lvr.request_measure("owner")
    out = lvr.run_pending()
    assert out["ran"] is False and "voice session" in out["why"]
    assert m.calls == 0
    assert "voice session" in lvr.job_status()["phase"]
    vlc.unregister("conv-1")
    assert lvr.run_pending()["ran"] is True and m.calls == 1


def test_the_job_waits_until_the_owner_has_been_away(monkeypatch):
    m = Measurer(monkeypatch)
    Assessor(monkeypatch)
    Admits(monkeypatch)
    monkeypatch.setattr(ls, "owner_idle_s", lambda: 10.0)
    monkeypatch.setattr(ls, "idle_window_s", lambda: 120.0)
    lvr.request_measure("owner")
    assert lvr.run_pending()["ran"] is False and m.calls == 0
    monkeypatch.setattr(ls, "owner_idle_s", lambda: 500.0)
    assert lvr.run_pending()["ran"] is True and m.calls == 1


def test_the_job_waits_while_a_persons_turn_runs(monkeypatch):
    m = Measurer(monkeypatch)
    Admits(monkeypatch)
    monkeypatch.setattr(ls, "interactive_busy_reason", lambda: "Friday is answering you")
    lvr.request_measure("owner")
    out = lvr.run_pending()
    assert out["ran"] is False and "answering you" in out["why"] and m.calls == 0


def test_a_call_that_opens_while_the_job_waits_for_its_turn_stops_it_before_it_loads_anything(monkeypatch):
    m = Measurer(monkeypatch)
    Assessor(monkeypatch)
    adm = Admits(monkeypatch, during=_open_call)          # the call starts while admit() waits
    lvr.request_measure("owner")
    out = lvr.run_pending()
    assert out["ran"] is False and m.calls == 0
    assert adm.released == 1, "the slot is given back"
    assert lvr.job_status()["wanted"] is True, "still wanted: it runs after the call"


def test_the_job_waits_for_memory(monkeypatch):
    m = Measurer(monkeypatch)
    Admits(monkeypatch)
    ls.set_ram_probe(lambda: 0.5)
    lvr.request_measure("owner")
    assert lvr.run_pending()["ran"] is False and m.calls == 0


# ── 4. the one switch offer ──────────────────────────────────────────────────

class Pushes:
    def __init__(self, monkeypatch):
        self.cards = []
        import agent_friday.notifications_engine as ne
        monkeypatch.setattr(ne, "push", lambda **kw: self.cards.append(kw) or kw)
        monkeypatch.setattr(ne, "resolve", lambda key: self.cards.append({"resolved": key}) or 1)


def _ready(monkeypatch, snap=None, numbers=None):
    Assessor(monkeypatch, snap=snap if snap is not None else CAPABLE_SNAP)
    monkeypatch.setattr(lvr, "measured_numbers", lambda profile=None: numbers if numbers is not None else NUMBERS)


def test_the_offer_goes_out_once_with_the_measured_numbers(monkeypatch):
    store = Store(monkeypatch)
    p = Pushes(monkeypatch)
    _ready(monkeypatch)
    assert lvr.maybe_offer_switch() is True
    assert len(p.cards) == 1
    card = p.cards[0]
    assert card["emitter"] == "LV7" and card["dedupe_key"] == "local-voice-switch-offer"
    assert card["kind"] == "local_voice_switch_offer"
    assert "480 MB" in card["body"] and "2.1 s" in card["body"] and "1.4 s" in card["body"]
    assert "private" in card["body"] and "costs nothing" in card["body"]
    assert store.data[lvr.OFFER_KEY] == "pending"
    assert lvr.maybe_offer_switch() is False, "once"
    assert len(p.cards) == 1


def test_the_offer_is_a_needs_you_notice_that_is_not_safety_critical():
    from agent_friday.services import notice_registry as reg
    em = reg.get("LV7")
    assert em is not None and em.place == reg.NEEDS_YOU and em.safety_critical is False
    assert em.kind == "local_voice_switch_offer" and em.key == "local-voice-switch-offer"
    assert "time" not in em.key and "{title" not in em.key
    assert "local_voice_switch_offer" not in reg.SAFETY_CRITICAL_KINDS
    assert reg.resolve("local_voice_switch_offer", "local-voice").id == "LV7"


def test_the_real_engine_files_it_under_needs_you_and_collapses_a_repeat(friday_dir, tmp_path, monkeypatch):
    from agent_friday import notifications_engine as ne
    from agent_friday.services import activity_ledger as al
    from agent_friday.services import notification_policy as pol
    if ne.NOTIF_FILE.exists():
        ne.NOTIF_FILE.unlink()
    monkeypatch.setattr(pol, "_LAST_TURN", [0.0])
    monkeypatch.setattr(al, "LEDGER_FILE", tmp_path / "activity_ledger.jsonl")
    monkeypatch.setattr(pol, "muted_kinds", lambda: set())
    Store(monkeypatch)
    _ready(monkeypatch)
    assert lvr.maybe_offer_switch() is True
    cards = ne.list_notifications(limit=20)
    assert len(cards) == 1
    assert cards[0]["place"] == "needs_you" and cards[0]["emitter"] == "LV7"
    assert not cards[0].get("safety_critical")
    again = ne.push(title="x", body="y", source="local-voice", kind="local_voice_switch_offer",
                    emitter="LV7", dedupe_key="local-voice-switch-offer")
    assert len(ne.list_notifications(limit=20)) == 1 and again.get("count", 1) >= 2


@pytest.mark.parametrize("why,kw", [
    ("not measured", {"snap": UNKNOWN_SNAP}),
    ("refused", {"snap": REFUSED_SNAP}),
    ("no measured numbers", {"numbers": {"measured": False, "stt": None, "tts": None}}),
])
def test_no_offer_until_local_voice_is_measured_capable(monkeypatch, why, kw):
    store = Store(monkeypatch)
    p = Pushes(monkeypatch)
    _ready(monkeypatch, **kw)
    assert lvr.maybe_offer_switch() is False and p.cards == []
    assert store.data[lvr.OFFER_KEY] == ""


@pytest.mark.parametrize("engine", ["local", "local-gpu", "auto"])
def test_no_offer_when_the_owner_is_not_on_cloud_voice(monkeypatch, engine):
    Store(monkeypatch, voice_engine=engine)
    p = Pushes(monkeypatch)
    _ready(monkeypatch)
    assert lvr.maybe_offer_switch() is False and p.cards == []


@pytest.mark.parametrize("state", ["pending", "accepted", "declined"])
def test_an_offer_already_made_is_never_made_again(monkeypatch, state):
    Store(monkeypatch, **{lvr.OFFER_KEY: state})
    p = Pushes(monkeypatch)
    _ready(monkeypatch)
    assert lvr.maybe_offer_switch() is False and p.cards == []


def test_declined_means_never_again_even_after_more_measuring(monkeypatch):
    store = Store(monkeypatch)
    p = Pushes(monkeypatch)
    _ready(monkeypatch)
    lvr.maybe_offer_switch()
    assert lvr.decide_offer("stay")["ok"] is True
    assert store.data[lvr.OFFER_KEY] == "declined" and store.data["voice_engine"] == "gemini"
    n = len(p.cards)
    Admits(monkeypatch)
    for _ in range(3):
        lvr.request_measure("owner")
        Measurer(monkeypatch)
        lvr.run_pending()
        lvr.maybe_offer_switch()
    assert [c for c in p.cards[n:] if c.get("dedupe_key")] == [], "no second card, ever"
    assert lvr.offer_card() is None


def test_the_card_has_both_choices_and_nothing_preselected(monkeypatch):
    Store(monkeypatch, **{lvr.OFFER_KEY: "pending"})
    monkeypatch.setattr(lvr, "measured_numbers", lambda profile=None: NUMBERS)
    card = lvr.offer_card()
    assert [c["id"] for c in card["choices"]] == ["switch", "stay"]
    for c in card["choices"]:
        assert not ({"selected", "checked", "default", "preselected", "active"} & set(c)), c
    assert sum(1 for c in card["choices"] if c["recommended"]) == 1, "one is labelled; none is chosen"
    assert card["numbers"]["stt"]["host_ram_mib"] == 480.0
    assert "480 MB" in card["body"]


def test_the_card_is_shown_only_while_it_is_pending_and_the_owner_is_on_cloud(monkeypatch):
    store = Store(monkeypatch)
    assert lvr.offer_card() is None
    store.data[lvr.OFFER_KEY] = "pending"
    monkeypatch.setattr(lvr, "measured_numbers", lambda profile=None: NUMBERS)
    assert lvr.offer_card() is not None
    store.data["voice_engine"] = "local"
    assert lvr.offer_card() is None


def test_nothing_in_the_background_ever_switches_the_engine(monkeypatch):
    store = Store(monkeypatch)
    Pushes(monkeypatch)
    _ready(monkeypatch)
    Measurer(monkeypatch)
    Admits(monkeypatch)
    monkeypatch.setattr(lvr, "hardware_signature", lambda profile=None: "sig")
    lvr.request_measure("owner")
    lvr.run_pending()
    lvr.tick(now=9e9)
    lvr.maybe_offer_switch()
    assert all("voice_engine" not in w for w in store.writes), store.writes
    assert store.data["voice_engine"] == "gemini"


def test_only_the_owners_yes_switches_and_it_resolves_the_card(monkeypatch):
    store = Store(monkeypatch, **{lvr.OFFER_KEY: "pending"})
    p = Pushes(monkeypatch)
    assert lvr.decide_offer("switch")["ok"] is True
    assert store.data["voice_engine"] == "local" and store.data[lvr.OFFER_KEY] == "accepted"
    assert {"resolved": "local-voice-switch-offer"} in p.cards


def test_an_answer_with_no_offer_waiting_is_refused(monkeypatch):
    store = Store(monkeypatch)
    assert lvr.decide_offer("switch")["ok"] is False
    assert lvr.decide_offer("maybe")["ok"] is False
    assert store.data["voice_engine"] == "gemini" and store.writes == []


def test_the_new_setting_has_a_default_and_a_docs_row():
    from agent_friday.core import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["local_voice_switch_offer"] == ""
    docs = (ROOT / "docs" / "user-guide" / "configuration.md").read_text(encoding="utf-8")
    assert "`local_voice_switch_offer`" in docs


# ── 5. why Friday is on cloud voice ──────────────────────────────────────────

def test_why_cloud_is_your_choice_when_local_would_run(monkeypatch):
    out = lvr.why_cloud(engine="gemini", settings={}, snapshot=CAPABLE_SNAP)
    assert out["on_cloud"] and out["reason"] == "your_choice" and out["action"] is None


def test_why_cloud_is_not_measured_with_a_measure_button(monkeypatch):
    out = lvr.why_cloud(engine="gemini", settings={}, snapshot=UNKNOWN_SNAP)
    assert out["reason"] == "local_not_measured"
    assert out["action"] == {"kind": "measure", "label": "Measure now"}
    assert "can't" not in out["text"].lower() and "cannot run" not in out["text"].lower()


def test_why_cloud_is_not_ready_with_the_reason_and_no_measure_button(monkeypatch):
    out = lvr.why_cloud(engine="gemini", settings={}, snapshot=REFUSED_SNAP)
    assert out["reason"] == "local_not_ready" and "too small" in out["text"] and out["action"] is None


def test_a_legacy_snapshot_reads_not_measured_not_not_ready():
    out = lvr.why_cloud(engine="gemini", settings={}, snapshot={
        "capable": False, "roles": {"voice": {"ok": False, "why": "capability check failed"}}})
    assert out["reason"] == "local_not_measured"


def test_local_only_mode_is_not_cloud_voice_whatever_the_engine_setting_says():
    s = {"voice_engine": "gemini", "model_routing": {"mode": "local_only"}}
    assert lvr.why_cloud(settings=s, snapshot=UNKNOWN_SNAP)["on_cloud"] is False
    assert lvr.why_cloud(engine="gemini", settings=s, snapshot=UNKNOWN_SNAP)["on_cloud"] is True, "the resolved engine wins"


def test_why_cloud_says_nothing_off_the_cloud():
    assert lvr.why_cloud(engine="local", settings={}, snapshot=UNKNOWN_SNAP)["on_cloud"] is False
    assert lvr.why_cloud(settings={"voice_engine": "local"}, snapshot=UNKNOWN_SNAP)["on_cloud"] is False
    assert lvr.why_cloud(settings={"voice_engine": "gemini"}, snapshot=UNKNOWN_SNAP)["on_cloud"] is True


def test_the_panel_status_is_one_object_for_the_panel_the_tool_and_session_info(monkeypatch):
    Store(monkeypatch, **{lvr.OFFER_KEY: "pending"})
    Assessor(monkeypatch, snap=UNKNOWN_SNAP)
    monkeypatch.setattr(lvr, "measured_numbers", lambda profile=None: NUMBERS)
    st = lvr.panel_status()
    assert st["voice_state"] == "unknown" and st["why_cloud"]["reason"] == "local_not_measured"
    assert st["job"]["state"] == "idle" and st["offer"] is not None
    assert st["assessed_at"] == UNKNOWN_SNAP["assessed_at"]


# ── 6. voice parity: the tool, found from the owner's own words ───────────────

@pytest.mark.parametrize("words", [
    "can you run voice locally now?",
    "can this PC run voice locally",
    "is local voice ready",
    "measure local voice",
    "please measure the local voice",
])
def test_the_tool_loader_finds_the_tool_from_both_phrasings(words):
    """`load_tools` ranks the on-demand tools by the words of the request; the two phrasings must
    find this tool first among its neighbours (the voice front's contract is full, so it is not
    declared there and not a Laya router tool: voice reaches it through ask_friday)."""
    from agent_friday.services import local_voice_tools as lvt
    from agent_friday.services import queue_tools
    from agent_friday.services import tool_catalogue as tc
    pool = list(lvt.TOOLS) + list(queue_tools.TOOLS) + [
        {"name": "local_model_status", "description": "Report the local model serving Friday's brain: seat, window, load.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "voice_preferences", "description": "Speak slower, faster, shorter or longer; how Friday talks.",
         "input_schema": {"type": "object", "properties": {}}}]
    found = tc.search(pool, words)
    assert found and found[0]["name"] == "local_voice_readiness", (words, [t["name"] for t in found])


def test_the_router_does_not_run_it_because_the_voice_contract_is_full():
    """The router only runs tools the voice front declares, and that contract has no room (its
    token ceiling test keeps 300 spare). Declaring this tool there is the lead's call, not LV-7's."""
    from agent_friday.services import laya_router as lr
    assert "local_voice_readiness" not in lr.TOOLS


def test_the_tool_says_not_measured_as_not_measured(monkeypatch):
    Assessor(monkeypatch, snap=UNKNOWN_SNAP)
    from agent_friday.services import local_voice_tools as lvt
    out = json.loads(lvt._tool_local_voice_readiness({"action": "status"}))
    assert out["state"] == "unknown"
    assert "not measured" in out["spoken"] and "measure local voice" in out["spoken"]
    assert "cannot run" not in out["spoken"].replace("cannot say", "")


def test_the_tool_says_why_when_it_is_a_no_and_yes_with_numbers_when_it_is_a_yes(monkeypatch):
    from agent_friday.services import local_voice_tools as lvt
    Assessor(monkeypatch, snap=REFUSED_SNAP)
    out = json.loads(lvt._tool_local_voice_readiness({}))
    assert out["state"] == "refused" and "too small" in out["spoken"]
    Assessor(monkeypatch, snap=CAPABLE_SNAP)
    monkeypatch.setattr(lvr, "measured_numbers", lambda profile=None: NUMBERS)
    out = json.loads(lvt._tool_local_voice_readiness({"action": "status"}))
    assert out["state"] == "capable" and "480 MB" in out["spoken"] and "your call" in out["spoken"]


def test_the_tool_measure_action_only_queues_it(monkeypatch):
    m = Measurer(monkeypatch)
    store = Store(monkeypatch)
    from agent_friday.services import local_voice_tools as lvt
    out = json.loads(lvt._tool_local_voice_readiness({"action": "measure"}))
    assert out["kind"] == "measure_queued" and "not run during a call" in out["spoken"]
    assert m.calls == 0 and lvr.job_status()["measure"] is True
    assert store.writes == []


def test_the_tool_switches_nothing_by_itself():
    """The tool can ask the cloud voice question and carry out an answer, but only the owner's own
    words in the turn answer it (tests/unit/test_cloud_voice_scope.py holds that); status and
    measure never touch the engine."""
    from agent_friday.services import local_voice_tools as lvt
    schema = lvt.TOOLS[0]["input_schema"]["properties"]["action"]["enum"]
    assert set(schema) == {"status", "measure", "cloud", "answer"}


def test_the_tool_is_registered_internal_and_loaded_on_demand():
    from agent_friday.governance import action_gate as ag
    from agent_friday.services import agent
    assert "local_voice_readiness" in agent.CLAUDE_TOOL_HANDLERS
    assert agent.TOOL_RINGS["local_voice_readiness"] == 1
    assert ag.classify("local_voice_readiness", {"action": "measure"})[0] == ag.INTERNAL
    assert "local_voice_readiness" in agent.ON_DEMAND_TOOLS
    assert not any(t.get("name") == "local_voice_readiness" for t in agent.CLAUDE_TOOLS)


# ── 7. the settings manifest and the UI ──────────────────────────────────────

def test_the_settings_manifest_carries_the_three_local_voice_actions():
    """The three UI actions are verbs in the settings manifest and rows in the UI-action fixture."""
    manifests = ROOT / "src" / "agent_friday" / "resources" / "laya_manifests"
    text = (manifests / "settings.json").read_text(encoding="utf-8")
    fixture = (ROOT / "tests" / "fixtures" / "laya_ws_ui_actions.json").read_text(encoding="utf-8")
    for vid in ("settings.local_voice_measure", "settings.local_voice_status",
                "settings.local_voice_switch_offer"):
        assert vid in text, vid
        assert vid in fixture, vid


def _component(html: str) -> str:
    a = html.index("function LocalVoiceReadiness(")
    return html[a:html.index("// ── Settings → Voice → Mode", a)]


def test_both_ui_files_carry_the_same_readiness_card_and_use_it():
    idx = (ROOT / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "ui_parts" / "app.html").read_text(encoding="utf-8")
    assert _component(idx) == _component(app), "index.html and ui_parts/app.html diverged"
    c = _component(idx)
    for needle in ("/api/voice/local-readiness", "/api/voice/local-readiness/measure",
                   "/api/voice/local-readiness/offer", "Measure now", "data-why-cloud",
                   "data-local-voice-offer", "offer.choices"):
        assert needle in c, needle
    assert "React.createElement(LocalVoiceReadiness" in idx
    assert "<LocalVoiceReadiness" in app


def test_the_ui_offers_measure_now_only_for_not_measured_and_preselects_nothing():
    c = _component((ROOT / "index.html").read_text(encoding="utf-8"))
    assert "st.voice_state === 'unknown' &&" in c
    assert "'active'" not in c and "settings-btn active" not in c, "no choice carries the chosen look"
    assert "(Recommended)" in c


def test_both_ui_files_word_the_consent_snapshot_by_state():
    for name in ("index.html", "ui_parts/app.html"):
        html = (ROOT / name).read_text(encoding="utf-8")
        assert "snapshot_state: st.snapshot_state || snap.state" in html, name
        assert "c.snapshot_state === 'refused'" in html, name
        assert "local was not measured when this was answered" in html, name
        assert "has not been measured for the full private-local set yet" in html, name
        assert "const measureNow" in html, name
