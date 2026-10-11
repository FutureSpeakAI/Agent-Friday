"""Local voice readiness: is this PC measured for local voice, and does the owner know.

The stored capability snapshot (`model_routing.cloud_consent.capability_snapshot`)
used to be written once, when the consent screen was answered, and never again.
A machine nobody had measured read "not capable", the owner stayed on cloud
voice, and nothing ever offered to measure or to switch. This module is the rest
of that story (Laya in every mode addendum, section 4, LV-7):

* **unknown is not refused.** The snapshot keeps `state` = capable / unknown /
  refused (privacy/cloud_consent). "Not measured" offers *Measure now*; it never
  reads as "can't".
* **Re-assessment refreshes the stored snapshot** when a local model is
  installed or measured (`note_event`), when the GPU, its driver or the RAM
  changes (`hardware_signature`), and weekly (`tick`). Re-assessing is not an
  answer: the owner's consent choice is never touched.
* **Everything runs as an idle background job** through the local scheduler
  (`local_scheduler.work` / `admit`, class `background`). There is no queue here,
  only the pending flags that say a job is wanted. The job never starts during a
  voice session, a person's turn, or before the owner has been away for the
  scheduler's idle window; it is listed in the scheduler's status and can be
  cancelled while it waits.
* **One switch offer.** When local voice becomes capable *with measured
  numbers* and the owner's engine is cloud, ONE card goes through the
  notifications engine (emitter LV7, needs_you, not safety-critical, stable
  dedupe key) showing those numbers and both choices, nothing preselected.
  Declined means never again (`local_voice_switch_offer` = "declined"). Nothing
  here ever changes `voice_engine` by itself: only `decide_offer("switch")`
  does, and only from the owner's click or spoken yes.
* **Why cloud?** `why_cloud` says why Friday is on cloud voice: your choice,
  local not ready, or local not measured (with the measure button).

Nothing here measures anything itself: `_measure_voice` is the one call to
`footprint_measure`, run only inside the idle job. Nothing leaves the machine.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("friday.local_voice_readiness")

WEEK_S = 7 * 24 * 3600.0
#: Free memory a voice measurement wants on top of the scheduler's own floor (GB).
MEASURE_NEED_GB = 1.0
JOB_LABEL = "Checking what this PC can run locally"
SOURCE = "local_capability"
JOB_ID = "local_capability:reassess"

OFFER_KEY = "local_voice_switch_offer"
OFFER_PENDING, OFFER_ACCEPTED, OFFER_DECLINED = "pending", "accepted", "declined"
OFFER_DEDUPE_KEY = "local-voice-switch-offer"
EMITTER_ID = "LV7"

REASON_CHOICE = "your_choice"
REASON_NOT_READY = "local_not_ready"
REASON_NOT_MEASURED = "local_not_measured"

CAPABLE, UNKNOWN, REFUSED = "capable", "unknown", "refused"

_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()
_OFFER_LOCK = threading.Lock()
_IN_JOB = [False]
_JOB: Dict[str, Any] = {"state": "idle", "kind": None, "phase": "", "ok": None,
                        "error": "", "queued_at": None, "started_at": None,
                        "finished_at": None}


# ── small readers ────────────────────────────────────────────────────────────

def _settings() -> dict:
    try:
        from agent_friday.core import _load_settings
        return _load_settings() or {}
    except Exception:
        return {}


def _state_path():
    from agent_friday.paths import friday_home
    return friday_home() / "runtime" / "local_voice_readiness.json"


def _load_state() -> dict:
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:  # pragma: no cover - defensive
        _log.debug("readiness state not saved: %s", e)


def _cc():
    from agent_friday.privacy import cloud_consent
    return cloud_consent


def _ls():
    from agent_friday.services import local_scheduler
    return local_scheduler


# ── what the snapshot says about voice ───────────────────────────────────────

def voice_state(snapshot: Optional[dict] = None) -> str:
    """capable / unknown / refused for LOCAL VOICE in the stored snapshot.

    A snapshot from before the states existed (no `state` on the role) only had
    `ok`; its False cannot be told from "not measured", so it reads as unknown
    until the next re-assessment replaces it."""
    snap = snapshot if snapshot is not None else _cc().stored_snapshot()
    if not isinstance(snap, dict):
        return UNKNOWN
    role = (snap.get("roles") or {}).get("voice")
    if not isinstance(role, dict):
        return UNKNOWN
    st = role.get("state")
    if st in (CAPABLE, UNKNOWN, REFUSED):
        return st
    return CAPABLE if role.get("ok") is True else UNKNOWN


def voice_why(snapshot: Optional[dict] = None) -> Optional[str]:
    snap = snapshot if snapshot is not None else _cc().stored_snapshot()
    role = ((snap or {}).get("roles") or {}).get("voice")
    return role.get("why") if isinstance(role, dict) else None


def measured_numbers(profile: Optional[dict] = None) -> dict:
    """What the voice measurement recorded: the ear (speech to text) and the
    voice (text to speech), each as measured on THIS machine. `measured` is True
    only when both rows are `basis == "measured"`; nothing is ever estimated
    here, so a missing number stays missing."""
    out: Dict[str, Any] = {"measured": False, "stt": None, "tts": None}
    try:
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.services import residency_catalog as rc
        from agent_friday.services import residency_policy as rp
        prof = profile if profile is not None else hwp.get()
        both = True
        for role, mid in (("stt", rp.DEFAULT_STT_MODEL), ("tts", rp.DEFAULT_TTS_MODEL)):
            fp = rc.footprint(mid, prof) or {}
            if fp.get("basis") != "measured":
                both = False
                continue
            out[role] = {"model_id": mid, "host_ram_mib": fp.get("host_ram_mib"),
                         "load_s": fp.get("load_s"), "measured_at": fp.get("measured_at")}
        out["measured"] = both
    except Exception as e:
        _log.debug("measured numbers unavailable: %s", e)
    return out


def _numbers_sentence(nums: dict) -> str:
    parts = []
    for role, word in (("stt", "listening"), ("tts", "speaking")):
        row = nums.get(role) or {}
        bits = []
        if row.get("load_s") is not None:
            bits.append("loads in %s s" % round(float(row["load_s"]), 1))
        if row.get("host_ram_mib") is not None:
            bits.append("uses about %d MB of memory" % round(float(row["host_ram_mib"])))
        if bits:
            parts.append("%s %s" % (word, " and ".join(bits)))
    return "; ".join(parts)


# ── why Friday is on cloud voice ─────────────────────────────────────────────

def why_cloud(*, engine: Optional[str] = None, settings: Optional[dict] = None,
              snapshot: Optional[dict] = None) -> dict:
    """Why the voice is on the cloud, in the owner's words.

    `engine` is the engine the session will really use (session-info passes its
    resolved one); without it the `voice_engine` setting is read, and local-only
    mode counts as not on the cloud (it blocks cloud voice outright). Not on
    cloud voice: `on_cloud` False and nothing else to say."""
    s = settings if settings is not None else _settings()
    eng = str(engine or s.get("voice_engine") or "local").strip().lower()
    if not engine and str((s.get("model_routing") or {}).get("mode") or "").strip().lower() == "local_only":
        eng = "local"
    if eng not in ("gemini", "gemini-live"):
        return {"on_cloud": False, "reason": None, "text": "", "action": None}
    state = voice_state(snapshot)
    if state == CAPABLE:
        return {"on_cloud": True, "reason": REASON_CHOICE, "state": state, "action": None,
                "text": "Cloud voice is your choice. Local voice would also run on this PC."}
    if state == UNKNOWN:
        return {"on_cloud": True, "reason": REASON_NOT_MEASURED, "state": state,
                "text": ("Local voice has not been measured on this PC yet, so there is no "
                         "telling whether it would run well. Measuring takes a few minutes "
                         "and runs when you are away."),
                "action": {"kind": "measure", "label": "Measure now"}}
    why = voice_why(snapshot)
    return {"on_cloud": True, "reason": REASON_NOT_READY, "state": state, "action": None,
            "text": "Local voice is not ready on this PC%s." % ((": " + why) if why else "")}


# ── hardware change signal ───────────────────────────────────────────────────

def hardware_signature(profile: Optional[dict] = None) -> str:
    """Changes when the GPU, its driver or the RAM does: the residency
    fingerprint (GPU name, VRAM, RAM) plus each GPU's driver version."""
    try:
        from agent_friday.services import hardware_profile as hwp
        from agent_friday.services import residency_catalog as rc
        prof = profile if profile is not None else hwp.get()
        parts = [rc.profile_fingerprint(prof)]
        for g in prof.get("gpus") or []:
            parts.append("%s@%s" % (g.get("name"), g.get("driver")))
        return "|".join(parts)
    except Exception:
        return ""


# ── the job: wanted, waiting, running ────────────────────────────────────────

def _set_job(**kw) -> None:
    with _LOCK:
        _JOB.update(kw)


def job_status() -> dict:
    """What the owner sees: idle, waiting (and for what), running, done, failed."""
    st = _load_state()
    pend = st.get("pending") or {}
    with _LOCK:
        j = dict(_JOB)
    wanted = bool(pend.get("measure") or pend.get("reasons"))
    if wanted and j["state"] in ("idle", "done", "failed", "cancelled"):
        j["state"] = "waiting"
        if not j.get("phase"):
            j["phase"] = _wait_phrase()
    j["wanted"] = wanted
    j["measure"] = bool(pend.get("measure"))
    j["reasons"] = list(pend.get("reasons") or [])
    j["last_assessed_at"] = st.get("assessed_at")
    return j


def _wait_phrase() -> str:
    ok, why = can_run_now()
    return "Waiting: " + why if not ok else "Ready to run"


def can_run_now() -> Tuple[bool, str]:
    """May the job start this moment? Never during a voice session or a person's
    turn, and not before the owner has been away for the scheduler's idle
    window. (Memory is the scheduler's own floor.)"""
    try:
        from agent_friday.services import voice_live_channel
        if voice_live_channel.any_live():
            return False, "a voice session is open"
    except Exception:
        pass
    ls = _ls()
    why = ls.interactive_busy_reason()
    if why:
        return False, why.lower()
    idle, need = ls.owner_idle_s(), ls.idle_window_s()
    if idle < need:
        return False, "you were here a moment ago; it starts after %d minutes away" % max(1, round(need / 60.0))
    mem = ls.memory_block(MEASURE_NEED_GB)
    if mem:
        return False, mem
    return True, ""


def _jobs_listing() -> List[dict]:
    st = _load_state()
    pend = st.get("pending") or {}
    if not (pend.get("measure") or pend.get("reasons")):
        return []
    with _LOCK:
        running = _JOB["state"] == "running"
        queued_at = _JOB.get("queued_at") or time.time()
        phase = _JOB.get("phase") or ""
    ok, why = (True, "") if running else can_run_now()
    return [{"id": JOB_ID, "source": SOURCE, "state": "running" if running else "waiting",
             "class": "background", "interactive": False, "label": JOB_LABEL,
             "kind": "local_capability", "model": "", "needs": "the local voice models",
             "reason": phase if running else why,
             "waited_s": round(max(0.0, time.time() - float(queued_at)), 1),
             "cancellable": not running}]


def _cancel_listed(job_id: str) -> bool:
    if job_id != JOB_ID:
        return False
    with _LOCK:
        if _JOB["state"] == "running":
            return False
    st = _load_state()
    if not st.get("pending"):
        return False
    st["pending"] = {}
    _save_state(st)
    _set_job(state="cancelled", phase="Taken out of the line by you.", finished_at=time.time())
    return True


def _want(reason: Optional[str], *, measure: bool = False) -> dict:
    with _LOCK:
        st = _load_state()
        pend = st.setdefault("pending", {})
        reasons = [r for r in (pend.get("reasons") or []) if r]
        if reason and reason not in reasons:
            reasons.append(reason)
        pend["reasons"] = reasons[-6:]
        if measure:
            pend["measure"] = True
        _save_state(st)
        if _JOB["state"] not in ("running",):
            _JOB.update(state="waiting", kind="measure" if pend.get("measure") else "reassess",
                        queued_at=_JOB.get("queued_at") if _JOB["state"] == "waiting" and _JOB.get("queued_at") else time.time(),
                        phase="", ok=None, error="")
    return job_status()


def request_reassess(reason: str = "requested") -> dict:
    """Ask for the snapshot to be refreshed in the next idle window. Cheap and
    non-blocking: it records that a job is wanted; `tick` runs it. A request made
    by the job itself (its own measurement writes a footprint) is ignored."""
    if _IN_JOB[0]:
        return job_status()
    return _want(str(reason or "requested")[:40])


def request_measure(reason: str = "owner") -> dict:
    """The owner's *Measure now*: measure local voice, then re-assess, as an idle
    background job. Never runs during a call."""
    if _IN_JOB[0]:
        return job_status()
    return _want(str(reason or "owner")[:40], measure=True)


def note_event(kind: str) -> None:
    """A local model was installed or measured: re-assess when idle. Called from
    the install and measurement paths; never raises."""
    try:
        request_reassess(kind)
    except Exception:
        pass


def _measure_voice() -> dict:
    """The one measuring call: both voice engines' footprint, real. Mocked by
    every test; runs only inside the idle job."""
    from agent_friday.services import footprint_measure as fpm
    return fpm.measure("voice")


def _visible_job():
    """The job's visible task (workspace Settings). A seam: tests stand it down."""
    try:
        from agent_friday.services.visible_jobs import visible_job
        return visible_job(JOB_LABEL, kind="local_capability", workspace="settings",
                           eager=True, deferrable=True, queued=False,
                           description="Measuring or re-checking what this PC can run locally.")
    except Exception:
        import contextlib
        return contextlib.nullcontext()


def _step(name: str) -> None:
    try:
        from agent_friday.services import visible_jobs
        visible_jobs.step(name)
    except Exception:
        pass


def run_pending() -> dict:
    """Run the wanted job if it may start now. Returns what happened.

    Single-flight. Passes the local scheduler's admission at class `background`
    (so it waits behind every person's turn and every other job), re-checks the
    idle gate after admission (a call may have started while it waited), then
    measures if asked, re-assesses and stores the snapshot, and finally lets the
    switch offer look at the result."""
    st = _load_state()
    pend = st.get("pending") or {}
    if not (pend.get("measure") or pend.get("reasons")):
        return {"ran": False, "why": "nothing is wanted"}
    ok, why = can_run_now()
    if not ok:
        _set_job(state="waiting", phase="Waiting: " + why)
        return {"ran": False, "why": why}
    if not _RUN_LOCK.acquire(blocking=False):
        return {"ran": False, "why": "already running"}
    _IN_JOB[0] = True
    ls = _ls()
    measured = False
    try:
        _set_job(state="running", started_at=time.time(), phase="Getting its turn",
                 kind="measure" if pend.get("measure") else "reassess", ok=None, error="")
        try:
            # Visible like every job: a task card, an orb and a row in the status
            # (services/visible_jobs). The card is made here, not by the
            # scheduler's own wrap, so it exists even when no Local AI queue runs.
            with _visible_job(), ls.work(JOB_LABEL, "background", quiet=False):
                grant = ls.admit("local_capability_reassess", cls="background", label=JOB_LABEL)
                try:
                    ok2, why2 = can_run_now()
                    if not ok2:
                        _set_job(state="waiting", phase="Waiting: " + why2)
                        return {"ran": False, "why": why2}
                    if pend.get("measure"):
                        _set_job(phase="Measuring local voice (the models load once)")
                        _step("Measuring local voice")
                        _measure_voice()
                        measured = True
                    _set_job(phase="Re-assessing what this PC can run")
                    _step("Re-assessing what this PC can run")
                    snap = _cc().refresh_snapshot()
                finally:
                    grant.release()
        except ls.LocalWorkCancelled:
            _set_job(state="cancelled", phase="Taken out of the line by you.", finished_at=time.time())
            return {"ran": False, "why": "cancelled"}
        st = _load_state()
        st["pending"] = {}
        st["assessed_at"] = time.time()
        sig = hardware_signature()
        if sig:
            st["signature"] = sig
        _save_state(st)
        offered = False
        try:
            offered = maybe_offer_switch(snap)
        except Exception as e:  # pragma: no cover - defensive
            _log.debug("switch offer skipped: %s", e)
        _set_job(state="done", ok=True, phase="Done", finished_at=time.time(), error="")
        return {"ran": True, "measured": measured, "state": (snap or {}).get("state"),
                "offered": offered}
    except Exception as e:
        _log.warning("local capability job failed: %s", e)
        _set_job(state="failed", ok=False, phase="Could not finish", finished_at=time.time(),
                 error=type(e).__name__)
        return {"ran": False, "why": "failed", "error": type(e).__name__}
    finally:
        _IN_JOB[0] = False
        _RUN_LOCK.release()


def tick(now: Optional[float] = None) -> dict:
    """One pass of the periodic check: raise a re-assessment for a hardware
    change or a week gone by (or a snapshot from before the states), then run
    whatever is wanted if the machine is idle. Cheap when nothing is due."""
    now = time.time() if now is None else float(now)
    st = _load_state()
    sig = hardware_signature()
    reasons = []
    if sig:
        if st.get("signature") and st["signature"] != sig:
            reasons.append("hardware")
        elif not st.get("signature"):
            st["signature"] = sig
            _save_state(st)
    last = st.get("assessed_at")
    if last is None or now - float(last) >= WEEK_S:
        legacy = _cc().snapshot_is_legacy(_cc().stored_snapshot())
        reasons.append("out_of_date" if legacy else "weekly")
    for r in reasons[:1]:
        request_reassess(r)
    return run_pending()


def loop(interval_s: float = 300.0, busy_interval_s: float = 20.0) -> None:  # pragma: no cover - thread body
    """The server's thread: tick, sleep short while a job waits, long otherwise."""
    time.sleep(90.0)                     # let the server settle before the first look
    while True:
        wait = interval_s
        try:
            out = tick()
            if out.get("ran") is False and out.get("why") not in ("nothing is wanted",):
                wait = busy_interval_s
        except Exception as e:
            _log.debug("readiness tick failed: %s", e)
        time.sleep(wait)


# ── the one-time switch offer ────────────────────────────────────────────────

def offer_state() -> str:
    return str(_settings().get(OFFER_KEY) or "")


def offer_card(settings: Optional[dict] = None) -> Optional[dict]:
    """The card the panel renders, or None when there is no offer to show:
    pending, the owner still on cloud voice. Both choices, nothing preselected."""
    s = settings if settings is not None else _settings()
    if str(s.get(OFFER_KEY) or "") != OFFER_PENDING:
        return None
    if str(s.get("voice_engine") or "local").strip().lower() not in ("gemini", "gemini-live"):
        return None
    nums = measured_numbers()
    return {"title": "Local voice now runs well on this PC",
            "body": _offer_body(nums), "numbers": nums,
            "choices": [{"id": "switch", "label": "Switch to local voice", "recommended": True},
                        {"id": "stay", "label": "Stay on cloud voice", "recommended": False}]}


def _offer_body(nums: dict) -> str:
    said = _numbers_sentence(nums)
    lead = "Local voice now runs well on this PC"
    if said:
        lead += ". Measured here: " + said
    return lead + ". It is private and costs nothing. Switch? Your call; nothing changes until you say."


def maybe_offer_switch(snapshot: Optional[dict] = None) -> bool:
    """Raise the ONE switch card when it is due. Never raises, never switches.

    Due means: local voice is capable in the snapshot, with measured numbers; the
    owner's engine is cloud; and no offer has ever been made (`""`). The state is
    set to pending BEFORE the push, so a second pass finds it taken; declined or
    accepted is forever."""
    try:
        with _OFFER_LOCK:
            s = _settings()
            if str(s.get(OFFER_KEY) or "") != "":
                return False
            if str(s.get("voice_engine") or "local").strip().lower() not in ("gemini", "gemini-live"):
                return False
            if voice_state(snapshot) != CAPABLE:
                return False
            nums = measured_numbers()
            if not nums.get("measured"):
                return False
            from agent_friday.core import _save_settings
            _save_settings({OFFER_KEY: OFFER_PENDING})
            body = _offer_body(nums)
        try:
            import agent_friday.notifications_engine as ne
            ne.push(title="Local voice now runs well on this PC", body=body,
                    priority="medium", source="local-voice", kind="local_voice_switch_offer",
                    emitter="LV7", dedupe_key=OFFER_DEDUPE_KEY,
                    resolve_key=OFFER_DEDUPE_KEY,
                    actions=[{"label": "See the choice", "workspace": "settings", "tab": "voice"}],
                    target={"workspace": "settings", "tab": "voice"})
        except Exception as e:
            _log.warning("switch offer card not pushed: %s", e)
        return True
    except Exception as e:  # pragma: no cover - defensive
        _log.debug("switch offer skipped: %s", e)
        return False


def decide_offer(choice: str) -> dict:
    """The owner's answer to the card. `switch` sets the voice engine to local;
    `stay` records "declined" for good. Only an offer that is pending can be
    answered; this is the only code that changes `voice_engine` for the offer."""
    choice = str(choice or "").strip().lower()
    if choice not in ("switch", "stay"):
        return {"ok": False, "message": "Say switch or stay."}
    if offer_state() != OFFER_PENDING:
        return {"ok": False, "message": "There is no offer waiting."}
    from agent_friday.core import _save_settings
    if choice == "switch":
        _save_settings({"voice_engine": "local", OFFER_KEY: OFFER_ACCEPTED})
    else:
        _save_settings({OFFER_KEY: OFFER_DECLINED})
    try:
        import agent_friday.notifications_engine as ne
        ne.resolve(OFFER_DEDUPE_KEY)
    except Exception:
        pass
    return {"ok": True, "choice": choice,
            "message": ("Switched to local voice." if choice == "switch"
                        else "Staying on cloud voice. I will not ask again.")}


# ── one status for the panel, the tool and session-info ──────────────────────

def panel_status(*, engine: Optional[str] = None) -> dict:
    snap = _cc().stored_snapshot()
    s = _settings()
    return {"voice_state": voice_state(snap), "why": voice_why(snap),
            "assessed_at": (snap or {}).get("assessed_at"),
            "why_cloud": why_cloud(engine=engine, settings=s, snapshot=snap),
            "job": job_status(), "offer": offer_card(s),
            "numbers": measured_numbers()}


def reset_for_tests() -> None:
    _IN_JOB[0] = False
    with _LOCK:
        _JOB.update(state="idle", kind=None, phase="", ok=None, error="",
                    queued_at=None, started_at=None, finished_at=None)
