"""U2: Laya's personal calibration, the promotion gate, and the visible refit job.

Spec: laya_swot_and_stack_spec_2026-10-10.md d.2 (U2), d.4 (promotion gate), c.4
(one calibration method), decisions 10 and 15. Rung: R1. Services/laya_personal
holds the rows and U1; this module reads the same rows.

WHAT A REFIT FITS, on the owner's own labelled turns (the training side of the
personal split; the sealed newest quarter is only ever scored):
  tau     the head's temperature (laya_calibration.fit_temperature)
  q_hat   its conformal cut-off at the target coverage
  act     the act threshold: the lowest value at which the unwanted-action rate on
          the owner's labels is within the target (2%)
Each row is scored with ITS OWN phrasing taken out of the prototypes
(leave-one-out), so a phrasing cannot vouch for itself.

INVARIANT 6, learning only tightens (c.2). ``clamp_to_shipped`` is the one place
a fitted value becomes an applied one: tau and q_hat can only go up from the
shipped table (flatter probabilities and larger conformal sets mean more asks),
and the act threshold can only go up from the effective shipped floor. A fit
that would loosen any of them is clamped, and the report card says what the data
would have asked for. Loosening is Decision 15: only the owner, per head, from
the report card, as a card of kind ``laya_threshold`` that only a click on the
screen (owner:ui) can approve; the approved card is the token
``laya_calibration.lower`` checks. Nothing here calls ``lower`` without one.

THE PROMOTION GATE (d.4), all of it, for a new U2 artifact:
  1. no new unwanted action on the shipped dev sets (1-3; set 5 is the lead's);
  2. no new unwanted action on the personal sealed set;
  3. better than live on personal sealed accuracy, but only with >= 50 sealed
     items on the routes it changes; below that U1/U2 are allowed, checked
     against the shipped sets (and this is recorded, not hidden);
  4. ECE no worse than live (when the sealed set is big enough to measure it; if
     not, tau is held at the live value and only the safe-by-construction
     tightenings go forward);
  5. invariant 6.
A passing candidate then runs in shadow for ``laya_personal_shadow_days`` (3)
before it swaps in atomically.

THE JOB (every job is visible). ``run_scheduled`` is a scheduler built-in
("laya_personal_refit", an idle-daily job). It registers in the Local AI queue
through services/background_gate, so the queue chip, the tray and the Activity
lane show it with its model name, status, trace and place in line. It runs only
when: personal learning is on, the vault is open (Decision 10: no overnight
unlock; a locked vault means it waits for the next idle window in which you are
signed in), the PC is on AC power, and it has the idle window the queue gives
deferrable work. It uses no GPU and no model seat: its "model" is the CPU
reading model Laya already shares.
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Sequence, Tuple

from agent_friday.services import laya_personal as lp
from agent_friday.services import laya_personal_store as store

_log = logging.getLogger("friday.laya_personal_fit")

HEAD = "router"
TARGET_UNWANTED = 0.02
MIN_ACTS = 10                     # acts at the shipped threshold before it may move
MIN_TEMP_ROWS = 20
MIN_ECE_ROWS = 10
MIN_ROUTE_SEALED = 50             # d.4 item 3
MIN_LOOSEN_LABELS = 50
ACT_CAP = 0.95
JOB_REF = "laya_personal_refit"
JOB_LABEL = "Laya: learning from your corrections"
#: The label the scheduler row and the task card carry (the manifest job and the scheduler row agree).
SCHEDULE_LABEL = "Learning from your corrections"
JOB_MODEL = "laya-personal"

on_ac_override = None
now_fn = time.time


# ---------------------------------------------------------------------------
#  invariant 6
# ---------------------------------------------------------------------------

def shipped_entry() -> dict:
    """The shipped table's numbers for the router head (tau, q_hat, act)."""
    try:
        from agent_friday.services import laya_calibration as lc
        e = lc.head_entry(HEAD) or {}
        return {"tau": float(e["tau"]) if e.get("tau") is not None else None,
                "q_hat": float(e["q_hat"]) if e.get("q_hat") is not None else None,
                "act": lc.shipped_threshold(HEAD, "act"),
                "act_effective": lc.effective_threshold(HEAD, "act")}
    except Exception:
        return {"tau": None, "q_hat": None, "act": None, "act_effective": None}


def clamp_to_shipped(fitted: dict, shipped: dict) -> Tuple[dict, List[str]]:
    """Make a fitted U2 safe to apply: nothing below the shipped value. Returns
    (applied values, the names that were clamped up)."""
    out, clamped = dict(fitted), []
    for k, floor_key in (("tau", "tau"), ("q_hat", "q_hat"), ("act", "act_effective")):
        v, floor = fitted.get(k), shipped.get(floor_key)
        if v is None:
            continue
        if floor is None:
            continue
        if float(v) < float(floor) - 1e-12:
            out[k] = float(floor)
            clamped.append(k)
    return out, clamped


def tightens_only(u2: Optional[dict], shipped: dict) -> bool:
    """Invariant 6 as a check (the gate's item 5)."""
    if not u2:
        return True
    for k, floor_key in (("tau", "tau"), ("q_hat", "q_hat"), ("act", "act_effective")):
        v, floor = u2.get(k), shipped.get(floor_key)
        if v is not None and floor is not None and float(v) < float(floor) - 1e-12:
            return False
    return True


# ---------------------------------------------------------------------------
#  the fit
# ---------------------------------------------------------------------------

def _gold_rows(rows: Sequence[dict]) -> List[dict]:
    """Only the owner's own gold labels back a loosening offer: a card approved in the same conversation
    is not matched to the turn, so a weak row proves nothing about one route."""
    return [r for r in _fit_rows(rows) if r.get("weight") == lp.GOLD]


def _fit_rows(rows: Sequence[dict]) -> List[dict]:
    return [r for r in rows if r.get("vec") is not None and r.get("text") and not r.get("off")
            and r.get("weight") != lp.SHADOW
            and ((r.get("outcome") in ("meant", "wanted") and lp._usable_label(r))
                 or (r.get("outcome") == "unwanted" and (r.get("routed") or {}).get("tool")))]


def _protos_for(row: dict, labels, base_protos, seed_counts, by_label: Dict[str, List[Tuple[str, list]]]):
    """The prototypes with every live phrasing except this row's own (leave-one-out)."""
    phr = {l: [v for rid, v in items if rid != row["id"]] for l, items in by_label.items()}
    return lp.adjusted_protos(labels, base_protos, seed_counts, phr)


def fit_u2(train_rows: Sequence[dict], labels, base_protos, seed_counts, live_phrasings: Sequence[dict],
           shipped: dict) -> Optional[dict]:
    """Fit tau, q_hat and the act threshold on the owner's labels. None when there
    is too little to move anything. The result is NOT yet safe: pass it through
    ``clamp_to_shipped``."""
    from agent_friday.services import laya_calibration as lc
    from agent_friday.services import laya_router as lr
    rows = _fit_rows(train_rows)
    if len(rows) < lp.MIN_SEALED_TOTAL:
        return None
    by_label: Dict[str, List[Tuple[str, list]]] = {}
    for p in live_phrasings:
        by_label.setdefault(p["label"], []).append((p["row"], p["vec"]))
    floor_act = float(shipped.get("act_effective") if shipped.get("act_effective") is not None else lr.ACT_THRESHOLD)
    scored = []
    for r in rows:
        protos = _protos_for(r, labels, base_protos, seed_counts, by_label)
        sims = [lp._dot(r["vec"], p) for p in protos]
        scored.append((r, sims, lr._rank_sims(labels, sims)))
    out: dict = {"head": HEAD, "n_rows": len(rows), "row_ids": sorted(r["id"] for r, _s, _k in scored),
                 "tau": None, "q_hat": None, "act": None, "fitted_at": now_fn()}
    # temperature and conformal set, on rows with a known answer
    truth_rows = [(s, labels.index(r["label"])) for r, s, _k in scored
                  if r.get("outcome") in ("meant", "wanted") and r.get("label") in labels]
    if len(truth_rows) >= MIN_TEMP_ROWS and shipped.get("tau") is not None:
        sims_rows = [s for s, _t in truth_rows]
        truth = [t for _s, t in truth_rows]
        tau = lc.fit_temperature(sims_rows, truth, base=shipped["tau"])
        scores = [1.0 - lc.probs_from_sims(s, max(tau, shipped["tau"]))[t] for s, t in truth_rows]
        out["tau"] = tau
        out["q_hat"] = lc.conformal_quantile(scores)
        out["ece"] = _ece(sims_rows, truth, max(tau, shipped["tau"]))
    # the act threshold: lowest value at which unwanted acts are within the target
    def acts_at(theta):
        n = bad = 0
        for r, _s, ranked in scored:
            route = lr._decide(ranked, r["text"], theta)
            if route.decision != "tool":
                continue
            n += 1
            case = _case_for(r)
            if lp.is_unwanted(("tool", route.tool), case):
                bad += 1
        return n, bad
    n0, bad0 = acts_at(floor_act)
    out["acts_at_shipped"], out["unwanted_at_shipped"] = n0, bad0
    if n0 >= MIN_ACTS:
        theta, steps = floor_act, 0
        while theta <= ACT_CAP + 1e-9:
            n, bad = acts_at(theta)
            if n == 0 or bad / n <= TARGET_UNWANTED:
                break
            theta, steps = round(theta + 0.01, 4), steps + 1
        out["act"] = min(max(theta, floor_act), ACT_CAP)
    return out


def _case_for(r: dict) -> lp.Case:
    if lp._usable_label(r) and r.get("outcome") in ("meant", "wanted"):
        return lp.Case(r["id"], r["text"], r["vec"], lp.expect_for_label(r["label"]), None, "personal")
    return lp.Case(r["id"], r["text"], r["vec"], None, (r.get("routed") or {}).get("tool"), "personal")


def _ece(sims_rows, truth, tau) -> float:
    from agent_friday.services import laya_calibration as lc
    conf, ok = [], []
    for s, t in zip(sims_rows, truth):
        p = lc.probs_from_sims(s, tau)
        conf.append(max(p))
        ok.append(p.index(max(p)) == t)
    return lc.ece(conf, ok)


# ---------------------------------------------------------------------------
#  the promotion gate
# ---------------------------------------------------------------------------

def gate(candidate: Optional[dict], live: Optional[dict], *, labels, protos,
         sealed_rows: Sequence[dict], shipped: dict) -> dict:
    """The d.4 gate for a new U2. ``protos`` are the prototypes the artifact will run
    with (shipped plus the live phrasings). Returns {ok, checks[], hold_tau}."""
    from agent_friday.services import laya_router as lr
    checks: List[dict] = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return ok

    if candidate is None:
        return {"ok": False, "checks": [{"name": "candidate", "ok": False, "detail": "Not enough of your labels to move anything yet."}],
                "hold_tau": True}
    base_act = float(shipped.get("act_effective") if shipped.get("act_effective") is not None else lr.ACT_THRESHOLD)
    cand_act = float(candidate.get("act") or base_act)
    live_act = float((live or {}).get("act") or base_act)
    # 1. shipped dev sets: the candidate's act threshold against the shipped one
    dev = lp.dev_cases()
    if dev:
        ref = lp.Rerun(labels, protos, dev, base_act)
        cand = ref.with_act(cand_act)
        res = lp.compare(ref.outcomes, cand.outcomes, ref.cases)
        add("shipped dev sets: no new unwanted action", not res["new_unwanted"],
            "%d new unwanted action(s) over %d questions" % (len(res["new_unwanted"]), len(dev)))
    else:
        add("shipped dev sets: no new unwanted action", False, "My shipped test questions could not be re-scored just now.")
    # 2. personal sealed set: against what is live
    cases = lp._sealed_cases(sealed_rows)
    if cases:
        ref = lp.Rerun(labels, protos, cases, live_act)
        cand = ref.with_act(cand_act)
        res = lp.compare(ref.outcomes, cand.outcomes, ref.cases)
        add("your sealed answers: no new unwanted action", not res["new_unwanted"],
            "%d new unwanted action(s) over %d of your answers" % (len(res["new_unwanted"]), len(cases)))
    else:
        add("your sealed answers: no new unwanted action", True, "You have no sealed answers yet; checked against my shipped sets only.")
    # 3. accuracy on the routes it changes (an act threshold changes none): recorded
    n_changed = 0
    add("accuracy on the routes it changes", True,
        "It changes no route, only how sure I must be to act (%d sealed items on changed routes; %d are needed to demand a gain)."
        % (n_changed, MIN_ROUTE_SEALED))
    # 4. ECE no worse than live, when it can be measured
    hold_tau = False
    truth_items = [(r, labels.index(r["label"])) for r in sealed_rows
                   if r.get("vec") is not None and r.get("label") in labels and r.get("outcome") in ("meant", "wanted")]
    if candidate.get("tau") is None:
        add("calibration (ECE) no worse", True, "No temperature change proposed.")
    elif len(truth_items) >= MIN_ECE_ROWS:
        sims_rows = [[lp._dot(r["vec"], p) for p in protos] for r, _t in truth_items]
        truth = [t for _r, t in truth_items]
        live_tau = (live or {}).get("tau") or shipped.get("tau")
        new = _ece(sims_rows, truth, candidate["tau"])
        old = _ece(sims_rows, truth, live_tau) if live_tau else None
        ok = old is None or new <= old + 1e-9
        add("calibration (ECE) no worse", ok, "ECE %.3f against %s live" % (new, "%.3f" % old if old is not None else "no"))
        hold_tau = not ok
    else:
        add("calibration (ECE) no worse", True,
            "Only %d sealed answers to measure it on (%d needed), so the temperature is held where it is." % (len(truth_items), MIN_ECE_ROWS))
        hold_tau = True
    # 5. invariant 6
    add("only ever tightens", tightens_only(candidate, shipped), "temperature, set size and act threshold are at or above the shipped values")
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "hold_tau": hold_tau}


# ---------------------------------------------------------------------------
#  the refit
# ---------------------------------------------------------------------------

def refit(profile: str, *, now: Optional[float] = None) -> dict:
    """Rebuild U1, fit U2 on the training rows, run the gate, and move a passing
    candidate through shadow to live. Needs the vault open. Returns a summary."""
    now = now if now is not None else now_fn()
    with lp._build_lock:
        ls = lp.learning_state(profile)
        if not ls["on"]:
            return {"status": "off", "why": ls["why"]}
        if not store.vault_open():
            return {"status": "locked", "why": "The vault is locked, so I do not train until it opens."}
        u1 = lp.rebuild(profile, "a scheduled refit")
        if u1.get("status") == "no_check":
            return {"status": "no_check", "why": u1["why"]}
        base = lp.base_state()
        if base is None:
            return {"status": "no_model", "why": "My reading model is not ready."}
        labels, base_protos, seed_counts = base
        rows = store.load_rows(profile) or []
        sealed, _rest = lp.personal_sealed(rows)
        sealed_ids = {r["id"] for r in sealed}
        train = [r for r in rows if r["id"] not in sealed_ids]
        st = lp._state(profile)
        live_art = lp._artifact(profile, st["live"]) if st.get("live") else None
        phr = (live_art or {}).get("phrasings") or []
        by_label: Dict[str, List[list]] = {}
        for p in phr:
            by_label.setdefault(p["label"], []).append(p["vec"])
        protos = lp.adjusted_protos(labels, base_protos, seed_counts, by_label)
        shipped = shipped_entry()
        fitted = fit_u2(train, labels, base_protos, seed_counts, phr, shipped)
        live_u2 = (live_art or {}).get("u2")
        out = {"status": "ok", "u1": u1, "fitted": bool(fitted), "promoted": False, "pending": False}
        if fitted is None:
            st["pending_u2"] = None
            st["last_gate"] = {"at": now, "ok": None, "checks": [], "note": "Not enough labels yet."}
            lp._save_state(profile, st)
            return out
        if fitted.get("tau") is None and fitted.get("act") is None:
            st["pending_u2"] = None
            st["last_gate"] = {"at": now, "ok": None, "checks": [], "note": "Your labels do not ask for anything to change yet."}
            lp._save_state(profile, st)
            return out
        applied, clamped = clamp_to_shipped(fitted, shipped)
        applied["clamped"] = clamped
        applied["asked_for"] = {k: fitted.get(k) for k in ("tau", "q_hat", "act") if k in clamped}
        verdict = gate(applied, live_u2, labels=labels, protos=protos, sealed_rows=sealed, shipped=shipped)
        if verdict["hold_tau"]:
            keep = (live_u2 or {}).get("tau") or shipped.get("tau")
            keepq = (live_u2 or {}).get("q_hat") or shipped.get("q_hat")
            applied["tau"], applied["q_hat"] = keep, keepq
        st["last_gate"] = {"at": now, "ok": verdict["ok"], "checks": verdict["checks"], "clamped": clamped}
        out["gate"] = verdict["ok"]
        if not verdict["ok"]:
            st["pending_u2"] = None
            lp._save_state(profile, st)
            return out
        same = live_u2 and all(abs(float(live_u2.get(k) or 0) - float(applied.get(k) or 0)) < 1e-9 for k in ("tau", "q_hat", "act"))
        if same:
            st["pending_u2"] = None
            lp._save_state(profile, st)
            return out
        days = _shadow_days()
        pend = st.get("pending_u2")
        if pend and all(abs(float(pend.get(k) or 0) - float(applied.get(k) or 0)) < 1e-9 for k in ("tau", "q_hat", "act")):
            since = float(pend.get("since") or now)
        else:
            since = now
            st["pending_u2"] = dict(applied, since=since)
        out["pending"] = True
        if now - since >= days * 86400.0:
            art = {"id": "a_%d_%s" % (int(now), "u2"), "created": now, "base": lp._base_version(), "reason": "a refit passed its gate and its shadow days",
                   "phrasings": phr, "u2": dict(applied, promoted_at=now),
                   "row_ids": sorted({p["row"] for p in phr} | set(applied.get("row_ids") or []))}
            st["pending_u2"] = None
            lp._commit_artifact(profile, st, art)
            out.update(promoted=True, pending=False, artifact=art["id"])
        else:
            lp._save_state(profile, st)
        return out


def _shadow_days() -> float:
    try:
        return max(0.0, float(lp.settings().get(lp.SETTING_SHADOW_DAYS, lp.DEFAULT_SHADOW_DAYS)))
    except (TypeError, ValueError):
        return float(lp.DEFAULT_SHADOW_DAYS)


# ---------------------------------------------------------------------------
#  the visible job
# ---------------------------------------------------------------------------

def on_ac_power() -> bool:
    """True on AC power. A desktop (no battery) counts as AC; an unreadable state
    counts as not-AC (training waits)."""
    if on_ac_override is not None:
        return bool(on_ac_override())
    try:
        import psutil
        b = psutil.sensors_battery()
        return b is None or bool(b.power_plugged)
    except Exception:
        return False


PRESENT_WITHIN_S = 30 * 60.0
presence_override = None


def session_unlocked() -> bool:
    """True when the Windows session is unlocked (the input desktop can be opened). Not Windows: True."""
    try:
        import sys
        if sys.platform != "win32":
            return True
        import ctypes
        h = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0001)
        if not h:
            return False
        ctypes.windll.user32.CloseDesktop(h)
        return True
    except Exception:
        return False                                        # cannot tell: treat as locked


def owner_present() -> bool:
    """Decision 10: training only while the owner is signed in. The cached vault key alone proves nothing
    (it is held for the life of the process, overnight too), so a run needs the Windows session
    unlocked AND the owner at the PC within the last 30 minutes. Idle but present is allowed; overnight
    is not."""
    if presence_override is not None:
        return bool(presence_override())
    if not session_unlocked():
        return False
    try:
        from agent_friday.services import background_gate
        return float(background_gate.idle_state()["idle_s"]) <= PRESENT_WITHIN_S
    except Exception:
        return False


def blocked_reason(profile: Optional[str] = None) -> str:
    """Why a refit may not start right now, or "". Every reason is "wait", never "fail"."""
    profile = profile or lp.current_profile()
    ls = lp.learning_state(profile)
    if not ls["on"]:
        return ls["why"]
    if not store.vault_open():
        return "the vault is locked (training waits for a time you are signed in)"
    if not on_ac_power():
        return "the PC is on battery"
    if not owner_present():
        return "you are not signed in and here (the Windows session is locked, or you have not been at the PC in the last 30 minutes)"
    try:
        from agent_friday.services import off_record
        if off_record.active():
            return "you are off the record"
    except Exception:
        pass
    return ""


def _sweep() -> None:
    """Old items age out whether or not learning is on. Deletion only: no rebuild, no embedding."""
    try:
        lp.enforce_retention(lp.current_profile() or lp.OWNER_PROFILE)
    except Exception as e:  # noqa: BLE001
        _log.debug("retention sweep skipped: %s", type(e).__name__)


def run_scheduled() -> dict:
    """The scheduler built-in. A visible job (services/visible_jobs, kind ``laya_personal_refit``): the
    Activity lane, the Local AI queue chip and the task card name it, its steps and its place in line.
    It waits (SkippedRun) rather than fails when a condition does not hold, and opens no card for a run
    that is not going to happen."""
    # The presence, AC-power, vault and learning checks come FIRST. Old items still age out when they
    # fail, but that is only a deletion (never a rebuild, never the reading model), so a locked,
    # unattended or on-battery PC does nothing but delete.
    why = blocked_reason()
    if why:
        _sweep()
        _skip("Laya's personal refit waits: " + why + ".")
        return {"status": "waiting", "why": why}
    from agent_friday.services import background_gate as bg
    from agent_friday.services.visible_jobs import note_inference, visible_job
    with visible_job("Learning from your corrections", kind="laya_personal_refit", workspace="settings",
                     deferrable=True, key=JOB_REF, label=JOB_LABEL):
        _sweep()
        # The reading model is the CPU encoder Laya already shares: no GPU, no model seat.
        note_inference(JOB_MODEL, local=True)
        try:
            bg.before_local_model_call(JOB_MODEL)         # waits in the Local AI queue for idle
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ == "JobCancelled":
                return {"status": "cancelled"}
            _log.debug("queue admission unavailable: %s", type(e).__name__)
        profile = lp.current_profile()
        why = blocked_reason(profile)                     # the world may have changed while it waited in line
        if why:
            return {"status": "waiting", "why": why}
        try:
            from agent_friday.services import reasoning_trace as rt
            with rt.scope("laya_personal", JOB_LABEL, nested=True, model=JOB_MODEL):
                res = refit(profile)
                lp.flush_skipped(profile)
                return res
        except ImportError:
            return refit(profile)


def _skip(message: str) -> None:
    try:
        from agent_friday.services import scheduler
        raise scheduler.SkippedRun(message)
    except ImportError:
        return


def register() -> None:
    """Register the built-in task (called from the scheduler's default roster)."""
    from agent_friday.services import scheduler
    scheduler.register_builtin_task(
        JOB_REF, run_scheduled, label=SCHEDULE_LABEL, default_trigger="idle_daily",
        default_spec={"idle_after_s": 600}, notify="silent")


# ---------------------------------------------------------------------------
#  Decision 15: loosening, only by the owner, per head, from the report card
# ---------------------------------------------------------------------------

def loosening_offers(profile: str, rows: Sequence[dict]) -> List[dict]:
    """What the report card may offer to loosen, per head. An offer exists only with
    enough of the owner's labels to back it; it is a proposal for a card, nothing
    more. The data never loosens anything by itself."""
    shipped = shipped_entry()
    eff, ship = shipped.get("act_effective"), shipped.get("act")
    if eff is None or ship is None:
        return []
    labelled = _gold_rows(rows)
    out = {"head": HEAD, "name": "act", "shipped": ship, "current": eff, "labels": len(labelled),
           "needed": MIN_LOOSEN_LABELS, "proposed": None, "can_offer": False,
           "why": ""}
    if len(labelled) < MIN_LOOSEN_LABELS:
        out["why"] = "I need at least %d of your labelled turns before I can suggest this (I have %d)." % (MIN_LOOSEN_LABELS, len(labelled))
        return [out]
    base = lp.base_state()
    if base is None:
        out["why"] = "My reading model is not ready."
        return [out]
    labels, base_protos, seed_counts = base
    st = lp._state(profile)
    live_art = lp._artifact(profile, st["live"]) if st.get("live") else None
    by_label: Dict[str, List[Tuple[str, list]]] = {}
    for p in (live_art or {}).get("phrasings") or []:
        by_label.setdefault(p["label"], []).append((p["row"], p["vec"]))
    from agent_friday.services import laya_router as lr
    scored = []
    for r in labelled:
        protos = _protos_for(r, labels, base_protos, seed_counts, by_label)
        scored.append((r, lr._rank_sims(labels, [lp._dot(r["vec"], p) for p in protos])))
    best = None
    theta = round(float(eff) - 0.01, 4)
    while theta >= 0.40:
        n = bad = 0
        for r, ranked in scored:
            route = lr._decide(ranked, r["text"], theta)
            if route.decision == "tool":
                n += 1
                bad += 1 if lp.is_unwanted(("tool", route.tool), _case_for(r)) else 0
        if n >= MIN_ACTS and bad == 0:
            best = theta
            theta = round(theta - 0.01, 4)
        else:
            break
    if best is None:
        out["why"] = "Your labels do not show a safe place to ask me for less, so I am not suggesting one."
    else:
        out.update(proposed=best, can_offer=True,
                   why="On %d of your own labelled turns, acting at %.2f instead of %.2f would not have acted wrongly once." % (len(labelled), best, eff))
    return [out]


def request_loosening(head: str, name: str, value: float) -> dict:
    """Raise the owner's card for loosening one head's act threshold. The card is
    screen-only (a click, never a spoken yes) and only the approved card is a token
    laya_calibration.lower will accept."""
    from agent_friday.services import approvals, laya_calibration as lc
    if head != HEAD or name != "act":
        return {"ok": False, "why": "That is not a threshold I learn."}
    shipped = shipped_entry()
    eff = shipped.get("act_effective")
    if eff is None or float(value) >= float(eff):
        return {"ok": False, "why": "That does not loosen anything."}
    from agent_friday.services import laya_router as _lr
    floor = max(0.40, float(_lr.ASK_THRESHOLD))             # never below the score at which an outward guess even asks
    if float(value) < floor:
        return {"ok": False, "why": "I will not act on less than %.2f." % floor}
    value = round(float(value), 4)
    rec = approvals.create_approval(
        kind=lc.APPROVAL_KIND, subject_type="laya_threshold", subject_id="%s:%s:%s" % (head, name, value),
        title="Let Laya act on a lower score for your reads", force_gate=True,
        description=("Laya now acts on a guess only when it scores at least %.2f. Your corrections suggest %.2f is as safe for you. "
                     "This lets her act without asking in a few more cases. Only you can approve this, only here, and you can raise it again any time."
                     % (float(eff), value)),
        action_description="lower Laya's act threshold from %.2f to %.2f" % (float(eff), value),
        action_class="internal", payload={"head": head, "name": name, "value": value}, requested_by="laya")
    return {"ok": True, "approval_id": rec["approval_id"], "status": rec["status"]}


def apply_approved_loosening(approval_id: str) -> dict:
    """Carry out an approved card: the card is the owner token. Nothing else loosens."""
    from agent_friday.services import approvals, laya_calibration as lc
    rec = approvals.get_approval(approval_id)
    if not rec or rec.get("kind") != lc.APPROVAL_KIND:
        return {"ok": False, "why": "That is not a threshold card."}
    p = rec.get("payload") or {}
    if rec.get("status") != "approved":
        return {"ok": False, "why": "That card is %s, not approved. Nothing changed." % (rec.get("status") or "undecided")}
    now_in_force = lc.lower(p.get("head"), p.get("name"), float(p.get("value")), owner_token=approval_id)
    lp.invalidate_thresholds()
    return {"ok": True, "in_force": now_in_force}


_HOOKED = False


WIPE_KIND = "laya_wipe_request"


def request_wipe(profile: str, n: int = 0) -> dict:
    """Raise the owner's card for a WIPE (decision: irreversible, so screen-only). The kind is in
    approvals.SCREEN_ONLY_KINDS, so only a click (owner:ui) approves it; a spoken yes cannot."""
    try:
        from agent_friday.services import approvals
        rec = approvals.create_approval(
            kind=WIPE_KIND, subject_type="laya_wipe", subject_id="%s:%d" % (profile, int(now_fn() // 3600)),
            title="Delete everything Laya learned about you", force_gate=True, action_class="internal",
            description=("This deletes the %d thing%s Laya learned from your corrections, the ones waiting in the vault buffer and every "
                         "saved version, for good. Reset would only set them aside. Only a click here approves it."
                         % (n, "" if n == 1 else "s")),
            action_description="delete everything Laya learned from the owner's corrections",
            payload={"profile": profile}, requested_by="laya")
        return {"ok": True, "approval_id": rec["approval_id"], "status": rec["status"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "a wipe card could not be raised (%s)" % type(e).__name__}


def _on_wipe_decision(record: dict) -> None:
    if (record or {}).get("kind") != WIPE_KIND or str(record.get("status") or "").lower() != "approved":
        return
    if record.get("decided_by") != "owner:ui":
        return                                              # belt and braces: only the screen click wipes
    out = lp.wipe(str((record.get("payload") or {}).get("profile") or lp.OWNER_PROFILE))
    lp.notify("done:wipe", "Wiped what Laya learned", "Laya's learning: everything she kept was deleted. She is back to how she shipped.")
    _log.info("laya wipe card %s: %s", record.get("approval_id"), out.get("ok"))


def _on_decision(record: dict) -> None:
    """The owner's click on a threshold card: carry it out. A denied card does nothing."""
    from agent_friday.services import laya_calibration as lc
    if (record or {}).get("kind") != lc.APPROVAL_KIND or str(record.get("status") or "").lower() != "approved":
        return
    if record.get("decided_by") != "owner:ui":
        return                                              # only a click on the screen loosens anything
    p = record.get("payload") or {}
    if p.get("head") != HEAD or p.get("name") != "act":
        return                                              # another head's card (the graduation token is lay1's), not ours
    try:
        res = apply_approved_loosening(record.get("approval_id"))
    except Exception as e:  # noqa: BLE001
        _log.warning("laya threshold card %s was not applied: %s", record.get("approval_id"), type(e).__name__)
        return
    _log.info("laya threshold card %s: %s", record.get("approval_id"), res.get("ok"))


def register_hooks() -> None:
    global _HOOKED
    if _HOOKED:
        return
    try:
        from agent_friday.services import approvals, laya_calibration as lc
        approvals.register_decision_hook(lc.APPROVAL_KIND, _on_decision)
        approvals.register_decision_hook(WIPE_KIND, _on_wipe_decision)
        _HOOKED = True
    except Exception as e:  # noqa: BLE001
        _log.warning("could not register the threshold-card hook: %s", e)


register_hooks()
