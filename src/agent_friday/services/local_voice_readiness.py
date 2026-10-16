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
* **Cloud voice is asked, never assumed.** Whatever starts the switch to cloud
  voice (Settings, the voice, the offer after a local voice failure) Friday asks
  "just this call, or always?", two choices, nothing preselected, one marked
  Recommended (just this call: it keeps local voice the default). "Just this call"
  puts the engine back to what it was when that cloud call ends; "always" is
  recorded as the owner's choice with the time and the reason
  (`voice_engine_choice`). Local-only mode refuses the switch with its reason.
* **Switch back, offered once.** After an "always", when a later re-assessment
  finds local voice capable again with measured numbers, ONE card offers to
  switch back (emitter LV7). It is remembered (`voice_switch_back`) and is not
  made again until readiness has changed again (two readings in a row that are not
  capable); no answer changes nothing. It is offered in every mode but local_only,
  cloud_only (the factory default) included, because local voice is still an engine
  the owner can choose there.
* **Voice answers.** The `local_voice_readiness` tool asks the question and takes
  the answers ("just this call", "always", "switch back", "keep cloud"), only on
  the owner's own words, spoken after the question and matched strictly
  (services/local_voice_tools).

Nothing here measures anything itself: `_measure_voice` is the one call to
`footprint_measure`, run only inside the idle job. Nothing leaves the machine.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
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

# Cloud voice is asked, never assumed: "just this call" or "always".
CHOICE_KEY = "voice_engine_choice"          # the owner's recorded switch to cloud voice
SWITCHBACK_KEY = "voice_switch_back"        # where the one switch-back offer stands
SCOPE_CALL, SCOPE_ALWAYS = "call", "always"
RECOMMENDED_SCOPE = SCOPE_CALL              # keeps local voice the default
SB_PENDING, SB_SWITCHED, SB_KEPT = "pending", "switched", "kept"
SCOPE_DEDUPE_KEY = "cloud-voice-scope"
CALL_END_DEDUPE_KEY = "cloud-voice-call-ended"
#: A "just this call" that no cloud call ever used goes back after this long, said aloud.
CALL_SCOPE_TTL_S = 6 * 3600.0
#: A local voice start that failed this recently is the reason a switch to cloud gives.
FAILURE_WINDOW_S = 3 * 3600.0

SWITCH_REASONS = {
    "local_voice_failed": "Local voice did not start",
    "your_choice": "Your choice",
}
CLOUD_ENGINES = ("gemini", "gemini-live")

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
        offered_back = False
        try:
            offered_back = maybe_offer_switch_back(snap, assessed_at=st["assessed_at"])
        except Exception as e:  # pragma: no cover - defensive
            _log.debug("switch-back offer skipped: %s", e)
        _set_job(state="done", ok=True, phase="Done", finished_at=time.time(), error="")
        return {"ran": True, "measured": measured, "state": (snap or {}).get("state"),
                "offered": offered, "offered_back": offered_back}
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
    elif _watching_for_switch_back() and now - float(last) >= SWITCH_BACK_RECHECK_S:
        reasons.append("switch_back_check")
    for r in reasons[:1]:
        request_reassess(r)
    try:
        expire_unused_call_scope(now)
        drop_stale_question()
    except Exception as e:  # pragma: no cover - defensive
        _log.debug("call scope expiry skipped: %s", e)
    return run_pending()


#: After an "always", how often an idle re-assessment looks again for local voice
#: being ready (the weekly check alone would leave the owner on cloud for days).
SWITCH_BACK_RECHECK_S = 24 * 3600.0


def _watching_for_switch_back() -> bool:
    """True while the owner is on cloud voice by an "always" and no switch-back
    offer has been made yet."""
    s = _settings()
    return (_record(s).get("scope") == SCOPE_ALWAYS and _is_cloud(s.get("voice_engine"))
            and not _record(s, SWITCHBACK_KEY) and _mode_of(s) != "local_only")


def loop(interval_s: float = 300.0, busy_interval_s: float = 20.0) -> None:  # pragma: no cover - thread body
    """The server's thread: tick, sleep short while a job waits, long otherwise."""
    reconcile_at_boot()                  # a "just this call" left by a crash or restart goes back now
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
            if _record(s):
                return False        # a cloud choice was recorded: its own switch-back offer owns this
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


# ── cloud voice: asked, never assumed ────────────────────────────────────────

#: A cloud voice question that has waited this long is gone (its card is closed).
QUESTION_TTL_S = 600.0
#: A spoken answer to the switch-back offer must come this soon after it was made.
SWITCH_BACK_VOICE_TTL_S = 24 * 3600.0
#: Owner words older than this are not an answer (a relay may carry an earlier utterance).
OWNER_WORDS_MAX_AGE_S = 120.0
#: Readings in a row that are not capable before a past switch-back offer is forgotten.
FLAP_READINGS = 2

#: Reads the question and applies its answer as one step, so a card click and a spoken
#: answer racing each other cannot both apply.
_ANSWER_LOCK = threading.RLock()


def _is_cloud(engine: Any) -> bool:
    return str(engine or "").strip().lower() in CLOUD_ENGINES


def _cloud_voice_connected() -> bool:
    """Is there a key the cloud voice can use (Google Gemini)? Fails open: a
    registry that cannot be read must not hide a question the owner needs."""
    try:
        from agent_friday.services.provider_registry import get_provider_registry
        return bool(get_provider_registry().is_provider_available("google-gemini"))
    except Exception:
        return True


def _mode_of(s: dict) -> str:
    """The routing mode in the owner's settings: local_only, local_preferred,
    cloud_preferred or cloud_only (a blank mode reads as the factory default)."""
    mr = (s or {}).get("model_routing")
    raw = mr.get("mode") if isinstance(mr, dict) else None
    try:
        from agent_friday.services import cloud_intent
        return cloud_intent.normalize_mode(raw)
    except Exception:
        return str(raw or "cloud_only").strip().lower() or "cloud_only"


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _save(patch: dict) -> None:
    from agent_friday.core import _save_settings
    _save_settings(patch)


def _resolve_card(key: str) -> None:
    try:
        import agent_friday.notifications_engine as ne
        ne.resolve(key)
    except Exception:
        pass


def _record(s: Optional[dict] = None, key: str = CHOICE_KEY) -> dict:
    v = (s if s is not None else _settings()).get(key)
    return dict(v) if isinstance(v, dict) and v else {}


def note_local_voice_failed() -> None:
    """A local voice session could not start. Remembered for a few hours so the
    switch to cloud voice can say why. Never raises."""
    try:
        with _LOCK:
            st = _load_state()
            st["local_failed_at"] = _now()
            _save_state(st)
    except Exception:
        pass


def _switch_reason() -> str:
    """Why voice is moving to the cloud, decided here and never taken from a caller."""
    failed = _load_state().get("local_failed_at")
    if failed is not None and _now() - float(failed) <= FAILURE_WINDOW_S:
        return "local_voice_failed"
    return "your_choice"


def scope_choices() -> List[dict]:
    """The two choices, in the order shown. Exactly one is marked recommended,
    and none is selected: the owner always picks."""
    return [
        {"id": SCOPE_CALL, "label": "Just this call",
         "recommended": RECOMMENDED_SCOPE == SCOPE_CALL,
         "detail": "Cloud voice for one call, then back to local voice. Local voice stays your default."},
        {"id": SCOPE_ALWAYS, "label": "Always",
         "recommended": RECOMMENDED_SCOPE == SCOPE_ALWAYS,
         "detail": "Cloud voice until you change it. I will tell you once if local voice is ready again."},
    ]


QUESTION = "Use cloud voice for just this call, or always?"


def spoken_question() -> str:
    return ("%s I would suggest just this call, because it keeps local voice as your default. "
            "Which do you want?" % QUESTION)


def local_only_refusal() -> dict:
    return {"ok": False, "refused": True, "reason": "local_only",
            "message": ("Local only mode is on, so voice stays on this PC and cloud voice is not "
                        "allowed. Change the routing mode in Settings first if you want it.")}


def _question(now: Optional[float] = None) -> Optional[dict]:
    """The waiting cloud voice question, or None when there is none or it has run out."""
    q = _load_state().get("cloud_question")
    if not isinstance(q, dict):
        return None
    now = _now() if now is None else float(now)
    if now > float(q.get("expires_at") or 0):
        return None
    return q


def cloud_question_card(settings: Optional[dict] = None) -> Optional[dict]:
    """The question the panel renders while one is waiting, else None."""
    s = settings if settings is not None else _settings()
    q = _question()
    if q is None or _is_cloud(s.get("voice_engine")) or _mode_of(s) == "local_only":
        return None
    return {"id": q.get("id"), "question": QUESTION, "choices": scope_choices(), "preselected": None,
            "reason": q.get("reason"), "reason_text": SWITCH_REASONS.get(q.get("reason"), ""),
            "source": q.get("source"), "asked_at": q.get("asked_at"),
            "expires_at": q.get("expires_at")}


def drop_stale_question() -> bool:
    """Take away a question that has run out, or that the mode or the engine has
    made moot, and close its screen card. Never raises."""
    try:
        with _ANSWER_LOCK:
            q = _load_state().get("cloud_question")
            if not isinstance(q, dict):
                return False
            s = _settings()
            gone = (_now() > float(q.get("expires_at") or 0) or _is_cloud(s.get("voice_engine"))
                    or _mode_of(s) == "local_only")
            if not gone:
                return False
            with _LOCK:
                st = _load_state()
                st.pop("cloud_question", None)
                _save_state(st)
        _resolve_card(SCOPE_DEDUPE_KEY)
        return True
    except Exception:  # pragma: no cover - defensive
        return False


def _digest(words: str) -> str:
    t = " ".join(str(words or "").lower().split())
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16] if t else ""


def asked_in_these_words(words: str) -> bool:
    """True when the waiting cloud voice question was asked in this very
    utterance. An answer must be a LATER thing the owner says: "use cloud voice
    for this call" asks, it does not answer."""
    q = _question()
    d = _digest(words)
    return bool(q is not None and d and q.get("words") == d)


def ask_cloud_switch(source: str = "settings", words: str = "") -> dict:
    """Start a switch to cloud voice: ask, do not switch. Whatever the source
    (Settings, the voice, the offer after a local voice failure) the answer is
    the owner's "just this call" or "always". Local-only mode refuses with its
    reason. The reason is worked out here, never taken from the caller. A screen
    card is raised for the sources that do not already have the panel in front of
    the owner. `words` is the utterance that started it, if any (see
    `asked_in_these_words`). The question has an id and runs out after
    QUESTION_TTL_S."""
    s = _settings()
    if _mode_of(s) == "local_only":
        return local_only_refusal()
    if _is_cloud(s.get("voice_engine")):
        return {"ok": True, "asked": False, "already": True,
                "message": "Voice is already on the cloud."}
    src = source if source in ("settings", "voice", "failure") else "settings"
    if src == "failure" and not _cloud_voice_connected():
        # Offering cloud voice to someone who never connected it would be a
        # question with no working answer: say nothing, raise no card.
        return {"ok": True, "asked": False, "not_connected": True,
                "message": ("Cloud voice isn't connected. You can add a Google "
                            "Gemini key in Settings > Connections.")}
    why = _switch_reason()
    now = _now()
    with _ANSWER_LOCK:
        with _LOCK:
            st = _load_state()
            st["cloud_question"] = {"id": _new_id(), "source": src, "reason": why, "asked_at": now,
                                    "expires_at": now + QUESTION_TTL_S, "words": _digest(words)}
            _save_state(st)
    if src != "settings":
        try:
            import agent_friday.notifications_engine as ne
            ne.push(title="Use cloud voice for just this call, or always?",
                    body=("%s. Just this call keeps local voice your default (Recommended). "
                          "Nothing changes until you choose." % SWITCH_REASONS[why]),
                    priority="medium", source="local-voice", kind="cloud_voice_scope_question",
                    emitter="LV8", dedupe_key=SCOPE_DEDUPE_KEY, resolve_key=SCOPE_DEDUPE_KEY,
                    actions=[{"label": "See the choice", "workspace": "settings", "tab": "voice"}],
                    target={"workspace": "settings", "tab": "voice"})
        except Exception as e:
            _log.warning("cloud voice question card not pushed: %s", e)
    return {"ok": True, "asked": True, "question": cloud_question_card(s), "message": spoken_question()}


def gate_settings_write(new_settings: dict) -> Optional[dict]:
    """Called by the generic settings write. A write that would move voice from
    local to cloud does not go through: it becomes the question (or, in
    local-only mode, a refusal). Returns None when the write may proceed, else
    {"http": status, "body": {...}}."""
    if not isinstance(new_settings, dict) or "voice_engine" not in new_settings:
        return None
    s = _settings()
    if not _is_cloud(new_settings.get("voice_engine")) or _is_cloud(s.get("voice_engine")):
        return None
    out = ask_cloud_switch(source="settings")
    if out.get("refused"):
        return {"http": 409, "body": {"status": "refused", "reason": out["reason"],
                                      "message": out["message"]}}
    return {"http": 409, "body": {"status": "needs_choice",
                                  "message": QUESTION + " Nothing has changed. Choose cloud voice on this PC's own screen, in Settings, Voice.",
                                  "question": out.get("question")}}


def answer_cloud_switch(choice: str, question_id: Optional[str] = None,
                        spoken_at: Optional[float] = None) -> dict:
    """The owner's answer: `call`, `always` or `cancel`. Only a waiting question
    can be answered. `question_id` (the page sends it) must be that question's;
    `spoken_at` (a spoken answer) must be after the question was asked. The
    question is read and the answer applied as one step. This (with the settings
    write it replaces) is the only code that moves the engine to the cloud."""
    choice = str(choice or "").strip().lower()
    if choice not in (SCOPE_CALL, SCOPE_ALWAYS, "cancel"):
        return {"ok": False, "message": "Say just this call, or always."}
    with _ANSWER_LOCK:
        s = _settings()
        q = _question()
        if q is None:
            drop_stale_question()
            return {"ok": False, "message": "There is no cloud voice question waiting."}
        if question_id is not None and str(question_id) != str(q.get("id")):
            return {"ok": False, "message": "That question has gone. Ask again."}
        if spoken_at is not None and not float(spoken_at) > float(q.get("asked_at") or 0):
            return {"ok": False, "stale": True,
                    "message": "I need your answer after I ask, not before."}
        with _LOCK:
            st = _load_state()
            st.pop("cloud_question", None)
            _save_state(st)
        _resolve_card(SCOPE_DEDUPE_KEY)
        if _mode_of(s) == "local_only":
            return local_only_refusal()
        if choice == "cancel":
            return {"ok": True, "choice": "cancel", "message": "Staying on local voice."}
        prev = str(s.get("voice_engine") or "local").strip() or "local"
        if _is_cloud(prev):
            prev = "local"
        rec = {"scope": choice, "engine": "gemini", "previous": prev, "at": _now(),
               "reason": q.get("reason") or "your_choice", "source": q.get("source") or "settings"}
        _save({"voice_engine": "gemini", CHOICE_KEY: rec, SWITCHBACK_KEY: {}})
    if choice == SCOPE_CALL:
        msg = "Cloud voice for this call. I will go back to local voice when it ends."
    else:
        msg = "Cloud voice, always. I will tell you once if local voice is ready again."
    return {"ok": True, "choice": choice, "message": msg}


def call_ended() -> bool:
    """A cloud voice call ended. If the owner chose "just this call", the engine
    goes back to what it was, and a line in Updates says so. Never raises."""
    try:
        s = _settings()
        rec = _record(s)
        if rec.get("scope") != SCOPE_CALL or not _is_cloud(s.get("voice_engine")):
            return False
        return _revert_call_scope(rec, "That cloud call has ended, so voice is back on local, as you chose.")
    except Exception as e:  # pragma: no cover - defensive
        _log.debug("call end revert skipped: %s", e)
        return False


def _revert_call_scope(rec: dict, said: str) -> bool:
    prev = str(rec.get("previous") or "local")
    _save({"voice_engine": prev, CHOICE_KEY: {}})
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title="Back on local voice", body=said, priority="low", source="local-voice",
                kind="cloud_voice_call_ended", emitter="LV9", dedupe_key=CALL_END_DEDUPE_KEY)
    except Exception as e:
        _log.warning("call end notice not pushed: %s", e)
    return True


def note_cloud_call_started() -> None:
    """A cloud voice call has begun. If it is covered by "just this call", the record
    remembers that a call really used it (so a restart in the middle of one is told
    apart from a choice made at setup that no call has used yet). Never raises."""
    try:
        s = _settings()
        rec = _record(s)
        if rec.get("scope") == SCOPE_CALL and _is_cloud(s.get("voice_engine")) and not rec.get("call_seen"):
            _save({CHOICE_KEY: dict(rec, call_seen=True)})
    except Exception:
        pass


def reconcile_at_boot() -> bool:
    """At start-up, a "just this call" that a live call used (so the server stopped in the
    middle of it) goes back to the engine it was, and says so; one that has run out goes
    back too. One that no call has used yet (chosen at setup, say) is left for its call.
    A question that ran out while Friday was off is closed. Never raises."""
    try:
        drop_stale_question()
        s = _settings()
        rec = _record(s)
        if rec.get("scope") != SCOPE_CALL or not _is_cloud(s.get("voice_engine")):
            return False
        if rec.get("call_seen"):
            return _revert_call_scope(
                rec, "Friday restarted during a cloud call, so voice is back on local, as you chose.")
        if _now() - float(rec.get("at") or _now()) >= CALL_SCOPE_TTL_S:
            return _revert_call_scope(
                rec, "No cloud call was started, so voice is back on local, as you chose.")
        return False
    except Exception as e:  # pragma: no cover - defensive
        _log.debug("boot reconcile skipped: %s", e)
        return False


def expire_unused_call_scope(now: Optional[float] = None) -> bool:
    """A "just this call" that no cloud call ever used must not hold the engine
    on the cloud for good: after CALL_SCOPE_TTL_S, with no voice session open, it
    goes back, and says so."""
    s = _settings()
    rec = _record(s)
    if rec.get("scope") != SCOPE_CALL or not _is_cloud(s.get("voice_engine")):
        return False
    now = _now() if now is None else float(now)
    if now - float(rec.get("at") or now) < CALL_SCOPE_TTL_S:
        return False
    try:
        from agent_friday.services import voice_live_channel
        if voice_live_channel.any_live():
            return False
    except Exception:
        pass
    return _revert_call_scope(rec, "No cloud call was started, so voice is back on local, as you chose.")


def note_engine_written(engine: Any) -> None:
    """The owner set the engine by hand. A move off the cloud ends the record of
    a cloud choice (and its switch-back offer): there is nothing to go back to.
    A question the write has made moot is closed."""
    try:
        drop_stale_question()
        if _is_cloud(engine):
            return
        s = _settings()
        if _record(s) or _record(s, SWITCHBACK_KEY):
            _save({CHOICE_KEY: {}, SWITCHBACK_KEY: {}})
            _resolve_card(OFFER_DEDUPE_KEY)
    except Exception:
        pass


# ── switch back, offered once ────────────────────────────────────────────────

def switch_back_card(settings: Optional[dict] = None) -> Optional[dict]:
    s = settings if settings is not None else _settings()
    sb = _record(s, SWITCHBACK_KEY)
    if sb.get("state") != SB_PENDING or not _is_cloud(s.get("voice_engine")):
        return None
    nums = measured_numbers()
    said = _numbers_sentence(nums)
    body = "Local voice is ready again on this PC"
    if said:
        body += ". Measured here: " + said
    body += ". Switch back? Your call; nothing changes until you say."
    return {"id": sb.get("id"), "title": "Local voice is ready again", "body": body, "numbers": nums,
            "preselected": None, "made_at": sb.get("made_at"),
            "choices": [{"id": "switch_back", "label": "Switch back to local voice", "recommended": True},
                        {"id": "keep_cloud", "label": "Keep cloud voice", "recommended": False}]}


def maybe_offer_switch_back(snapshot: Optional[dict] = None, *,
                            assessed_at: Optional[float] = None) -> bool:
    """Offer, ONCE, to switch back to local voice. Never raises, never switches.

    Due when: the owner chose "always" for cloud voice; the engine is still on
    the cloud; the routing mode is not local_only (cloud_only, the factory
    default, still allows local voice, so it is offered there too); a
    re-assessment AFTER that choice finds local voice capable with measured
    numbers; and no offer has been made since readiness last changed. The state
    is set to pending BEFORE the push, so a second pass finds it taken. A past
    offer is forgotten only after FLAP_READINGS readings in a row that are not
    capable, so one failed or stale measurement does not repeat it. No answer
    leaves everything as it was. The offer is one tray card; it is not also said
    into a call."""
    try:
        with _OFFER_LOCK:
            s = _settings()
            rec = _record(s)
            sb = _record(s, SWITCHBACK_KEY)
            if rec.get("scope") != SCOPE_ALWAYS:
                return False
            if not _is_cloud(s.get("voice_engine")):
                _save({CHOICE_KEY: {}, SWITCHBACK_KEY: {}})       # the owner already moved
                _resolve_card(OFFER_DEDUPE_KEY)
                return False
            if _mode_of(s) == "local_only":
                return False
            snap_state = voice_state(snapshot)
            if snap_state != CAPABLE:
                if sb:
                    flaps = int(sb.get("flaps") or 0) + 1
                    if flaps >= FLAP_READINGS:
                        _save({SWITCHBACK_KEY: {}})              # readiness changed: a later "capable" is news again
                        if sb.get("state") == SB_PENDING:
                            _resolve_card(OFFER_DEDUPE_KEY)
                    else:
                        _save({SWITCHBACK_KEY: dict(sb, flaps=flaps)})
                return False
            if sb:
                if sb.get("flaps"):
                    _save({SWITCHBACK_KEY: dict(sb, flaps=0)})
                return False
            when = assessed_at if assessed_at is not None else _load_state().get("assessed_at")
            if when is None or float(when) <= float(rec.get("at") or 0):
                return False                                     # not re-assessed since the switch
            nums = measured_numbers()
            if not nums.get("measured"):
                return False
            _save({SWITCHBACK_KEY: {"id": _new_id(), "state": SB_PENDING, "made_at": _now(),
                                    "choice_at": rec.get("at"), "flaps": 0}})
            body = switch_back_card(_settings())
        text = (body or {}).get("body") or "Local voice is ready again. Switch back?"
        try:
            import agent_friday.notifications_engine as ne
            ne.push(title="Local voice is ready again", body=text,
                    priority="medium", source="local-voice", kind="local_voice_switch_offer",
                    emitter="LV7", dedupe_key=OFFER_DEDUPE_KEY,
                    resolve_key=OFFER_DEDUPE_KEY,
                    actions=[{"label": "See the choice", "workspace": "settings", "tab": "voice"}],
                    target={"workspace": "settings", "tab": "voice"})
        except Exception as e:
            _log.warning("switch-back offer card not pushed: %s", e)
        return True
    except Exception as e:  # pragma: no cover - defensive
        _log.debug("switch-back offer skipped: %s", e)
        return False


def decide_switch_back(choice: str, question_id: Optional[str] = None,
                       spoken_at: Optional[float] = None) -> dict:
    """The owner's answer to the switch-back card: `switch_back` or `keep_cloud`.
    Only a pending offer can be answered. `question_id` (the page sends it) must be
    that offer's; `spoken_at` (a spoken answer) must be after the offer was made
    and within SWITCH_BACK_VOICE_TTL_S of it. Keeping cloud leaves the offer made,
    so it is not asked again until readiness has changed."""
    choice = str(choice or "").strip().lower()
    if choice not in ("switch_back", "keep_cloud"):
        return {"ok": False, "message": "Say switch back, or keep cloud."}
    with _ANSWER_LOCK:
        s = _settings()
        sb = _record(s, SWITCHBACK_KEY)
        if sb.get("state") != SB_PENDING:
            return {"ok": False, "message": "There is no switch-back offer waiting."}
        if question_id is not None and str(question_id) != str(sb.get("id")):
            return {"ok": False, "message": "That offer has gone."}
        if spoken_at is not None:
            made = float(sb.get("made_at") or 0)
            if not (made < float(spoken_at) <= made + SWITCH_BACK_VOICE_TTL_S):
                return {"ok": False, "stale": True,
                        "message": "I need your answer after I offer, not before."}
        rec = _record(s)
        if choice == "switch_back":
            prev = str(rec.get("previous") or "local")
            _save({"voice_engine": prev, CHOICE_KEY: {},
                   SWITCHBACK_KEY: dict(sb, state=SB_SWITCHED, answered_at=_now())})
            msg = "Switched back to local voice."
        else:
            _save({SWITCHBACK_KEY: dict(sb, state=SB_KEPT, answered_at=_now())})
            msg = "Keeping cloud voice. I will not ask again unless local voice changes."
    _resolve_card(OFFER_DEDUPE_KEY)
    return {"ok": True, "choice": choice, "message": msg}


# ── the owner's words, for the voice answers ─────────────────────────────────

#: Whole-word phrases. A phrase counts only as whole words, never inside a longer one.
_PHRASES = {
    SCOPE_CALL: (("just", "this", "call"), ("just", "for", "this", "call"), ("only", "this", "call"),
                 ("only", "for", "this", "call"), ("this", "call", "only"), ("for", "this", "call")),
    SCOPE_ALWAYS: (("always",), ("permanently",), ("from", "now", "on"), ("every", "call")),
    "switch_back": (("switch", "back"), ("go", "back", "to", "local"), ("back", "to", "local"),
                    ("switch", "to", "local")),
    "keep_cloud": (("keep", "cloud"), ("keep", "the", "cloud"), ("keep", "it", "on", "cloud"),
                   ("stay", "on", "cloud"), ("stay", "on", "the", "cloud"), ("stay", "with", "cloud")),
    "cancel": (("stay", "on", "local"), ("keep", "local"), ("stay", "local")),
}
#: A negation, a correction or a retraction ANYWHERE in the utterance, or a sign that someone
#: else's words are being repeated, makes it a doubt or a quote, never an answer. ("Never mind"
#: and "cancel" are corrections too: they give no answer, and Friday asks again.)
_NEGATIONS = frozenset({"no", "not", "dont", "never", "nope", "nah", "cant", "cannot", "wont", "without",
                        "isnt", "arent", "didnt", "doesnt", "shouldnt", "wouldnt", "stop", "stopped",
                        "wait", "actually", "hold", "cancel", "nevermind", "sorry", "correction",
                        "instead", "rather", "mistake", "oops", "undo",
                        "said", "says", "say", "told", "tells", "asked", "quote", "unquote", "quoted"})
#: Words that make a sentence a thought or a question rather than an answer.
_HEDGES = frozenset({"if", "maybe", "perhaps", "unless", "whether", "should", "would", "could", "might",
                     "wonder", "what", "why", "how", "who", "when", "which", "where"})
_QUOTES = ('"', "“", "”", "‘", "«", "»")
MAX_ANSWER_WORDS = 8


def classify_answer(words: str) -> Optional[str]:
    """Which answer the owner's words give: `call`, `always`, `switch_back`,
    `keep_cloud` or `cancel`. Strict: whole words only and a short, clean utterance.
    Nothing is an answer if it holds a question mark, a quote, a hedge, or any
    negation, correction or retraction word anywhere ("no, not always", "always, no,
    wait", "don't switch back", "she said always"), or gives two answers. Then Friday
    asks again. Matched on the owner's own utterance, never on a model's paraphrase."""
    raw = str(words or "")
    if not raw.strip() or "?" in raw or any(q in raw for q in _QUOTES):
        return None
    flat = raw.lower().replace("'", "").replace("’", "")
    toks = "".join(ch if ch.isalnum() else " " for ch in flat).split()
    if not toks or len(toks) > MAX_ANSWER_WORDS:
        return None
    if any(t in _HEDGES or t in _NEGATIONS for t in toks):
        return None
    hits = set()
    for cid, phrases in _PHRASES.items():
        for ph in phrases:
            n = len(ph)
            for i in range(len(toks) - n + 1):
                if tuple(toks[i:i + n]) == ph:
                    hits.add(cid)
    return next(iter(hits)) if len(hits) == 1 else None


# ── one status for the panel, the tool and session-info ──────────────────────

def panel_status(*, engine: Optional[str] = None) -> dict:
    snap = _cc().stored_snapshot()
    s = _settings()
    return {"voice_state": voice_state(snap), "why": voice_why(snap),
            "assessed_at": (snap or {}).get("assessed_at"),
            "why_cloud": why_cloud(engine=engine, settings=s, snapshot=snap),
            "job": job_status(), "offer": offer_card(s),
            "numbers": measured_numbers(),
            "cloud_question": cloud_question_card(s),
            "switch_back": switch_back_card(s),
            "cloud_choice": cloud_choice_summary(s)}


def cloud_choice_summary(settings: Optional[dict] = None) -> Optional[dict]:
    """The owner's recorded switch to cloud voice, for the panel: scope, when,
    and why. None when voice is not on the cloud by a recorded choice."""
    s = settings if settings is not None else _settings()
    rec = _record(s)
    if not rec or not _is_cloud(s.get("voice_engine")):
        return None
    return {"scope": rec.get("scope"), "at": rec.get("at"), "reason": rec.get("reason"),
            "reason_text": SWITCH_REASONS.get(rec.get("reason"), ""), "source": rec.get("source")}


def reset_for_tests() -> None:
    _IN_JOB[0] = False
    with _LOCK:
        _JOB.update(state="idle", kind=None, phase="", ok=None, error="",
                    queued_at=None, started_at=None, finished_at=None)
