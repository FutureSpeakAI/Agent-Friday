"""
Closed-loop learning — trajectory capture + nightly skill optimization.

Connects the previously-dormant SkillOpt machinery to the live chat loop:

    capture turn  ->  record real-usage metrics on matched skills
                  ->  nightly auto-research tick (the "Karpathy loop")

Retrieval/injection of learned skills is handled separately by ``skill_registry``
(skills whose triggers match the message are injected into the system prompt each
turn). This module closes the *other* half of the loop: it feeds real chat usage
back into SkillOpt's scoring + research, which were fully built but never invoked.

Everything here is best-effort and silent — it must never raise into the chat
path. No LLM call is required: SkillOpt's research loop has a heuristic fallback
that runs when no researcher callable is supplied.
"""

import json
import time
import threading
from agent_friday.paths import friday_home

FRIDAY_DIR = friday_home()
TRAJ_FILE = FRIDAY_DIR / "trajectories.jsonl"

_LOCK = threading.Lock()
_MAX_TRAJ_KEEP = 5000

# Reply prefixes that mean the turn did NOT succeed.
_DENY_PREFIXES = ("[GOVERNANCE DENY]", "[SANDBOX DENY]", "Tool error", "[Friday offline]")

# Maintainer ruling: _success_score() has no real evidence of task
# completion for ANY reply -- it only detects
# CONFIRMED failure signals (an error, an obviously-truncated reply, a
# refusal prefix). Labeling everything else 1.0 ("success") was not a weak
# signal, it was a false one: "Done. I sent the email and booked your
# flight." scored identically whether either action happened, because
# nothing here ever checked. The honest minimum fix: a third state for "no
# confirmed failure, but also no confirmed success" -- the absence of a bad
# signal is not the presence of a good one. UNVERIFIED_SCORE is reachable
# and is now what a plausible-looking reply actually gets; SUCCESS_SCORE
# stays fully wired through every consumer (composite_score, trajectory_
# stats) but nothing in this file can currently produce it. Real completion
# verification -- checking tool_trace against what the reply claims,
# criteria specific to what a skill/task type actually promises -- is a
# real design question, specified as follow-up work below, not decided
# here.
FAILURE_SCORE = 0.0
UNVERIFIED_SCORE = 0.5
SUCCESS_SCORE = 1.0

# Follow-up (deferred by maintainer ruling): what "verified" should
# actually require, by task shape, so SUCCESS_SCORE has a real path
# to being produced instead of sitting permanently unreachable:
#   * Action claims ("I sent/booked/created/deleted X") -- require a
#     matching tool_trace entry whose own reported outcome is success, not
#     just that A tool was called. A reply claiming an email was sent with
#     an empty tool_trace, or a trace showing the send tool errored, is
#     grounds for FAILURE_SCORE, not UNVERIFIED_SCORE -- that is a
#     confirmable false claim, a stronger signal than "no evidence either
#     way".
#   * Informational answers (no action claimed) -- no tool_trace is
#     required or expected; SUCCESS_SCORE here would need a real
#     correctness/completeness check against the question asked, which has
#     no generic implementation (it is necessarily task/skill-specific).
#   * Any task with an explicit, checkable side effect (a file written, a
#     calendar event created) could verify directly by reading the side
#     effect back, mirroring store_key()'s own "read it back, don't just
#     trust the write" pattern elsewhere in this codebase.
# This needs a real design decision (what counts as sufficient evidence
# per skill/task type, and whether a confirmable false claim should be
# FAILURE_SCORE rather than UNVERIFIED_SCORE) before any of it is built.


def _success_score(reply, error):
    if error:
        return FAILURE_SCORE
    r = (reply or "").strip()
    if len(r) < 8:
        return FAILURE_SCORE
    if r.startswith(_DENY_PREFIXES):
        return FAILURE_SCORE
    return UNVERIFIED_SCORE


def _append_jsonl(rec):
    with _LOCK:
        try:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            with open(TRAJ_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            pass


def capture(message, reply, tool_trace=None, duration_ms=None, error=None, workspace=None,
            task_id=None, orb_id=None, session_id=None, model=None):
    """Record one task trajectory and accumulate metrics on matched skills.

    Best-effort and silent — never raises into the chat path.

    task_id / orb_id / session_id / model (all optional, B3): correlation ids
    linking this trajectory to the background task, the process orb, the chat
    session and the model that served the turn. Included in the record only
    when set, so existing callers (routes/chat.py's daemon thread) are
    unchanged until they opt in.
    """
    try:
        score = _success_score(reply, error)
        tools = []
        for t in (tool_trace or []):
            if isinstance(t, dict):
                nm = t.get("name") or t.get("tool")
                if nm:
                    tools.append(nm)
        rec = {
            "ts": time.time(),
            "message": (message or "")[:2000],
            "reply_len": len(reply or ""),
            "tools": tools,
            "duration_ms": duration_ms,
            "success": score,
            "error": error,
            "workspace": workspace,
        }
        for _k, _v in (("task_id", task_id), ("orb_id", orb_id),
                       ("session_id", session_id), ("model", model)):
            if _v is not None:
                rec[_k] = _v
        _append_jsonl(rec)

        # Tamper-evident copy in cognitive memory (provenance + audit trail).
        try:
            from agent_friday.cognitive_memory import get_cognitive_memory
            get_cognitive_memory().write_memory(
                key=f"trajectory/{int(rec['ts'] * 1000)}",
                content=json.dumps(rec, default=str),
                source_id="skill_capture",
                metadata={"success": score, "tools": tools},
            )
        except Exception:
            pass

        # Accumulate real-usage metrics on any registry skill this message
        # matched, so SkillOpt scores reflect live chat usage (not just the
        # bundled batch engines).
        try:
            import agent_friday.skill_registry as skreg
            matched = skreg.match_skills(message, limit=3)
            if matched:
                from agent_friday.skillopt_engine import record_skill_run
                for sk in matched:
                    record_skill_run(
                        skill_name=sk.name,
                        inputs={"message": (message or "")[:500], "tools": tools},
                        outputs={"reply_len": rec["reply_len"]},
                        # The score must land on a key composite_score()
                        # actually reads (accuracy, user_satisfaction,
                        # completeness, latency, cost). Sending
                        # {"quality": score, "success": score} makes
                        # score=1.0 and score=0.0 produce the IDENTICAL
                        # composite score (0.25 with default weights).
                        # `accuracy` is the one
                        # dimension _success_score()'s semantics (no error,
                        # non-trivial reply, no refusal prefix) can honestly
                        # speak to -- it is deliberately NOT also copied into
                        # user_satisfaction/completeness, which would fabricate
                        # confidence this heuristic has no information about.
                        # _success_score() itself remains a reply-shape check,
                        # not real task verification (a separate, open
                        # question -- see F75) -- this fix only ensures the
                        # existing signal, weak as it is, is no longer
                        # silently discarded before scoring.
                        metrics={"accuracy": score},
                        duration_ms=float(duration_ms or 0.0),
                        error=error,
                    )
        except Exception:
            pass
    except Exception:
        pass


def run_nightly():
    """Trigger auto-research on every SkillOpt skill whose scores have drifted.

    Activates the previously-uninvoked Karpathy research loop. Returns a summary
    dict for logging/tests.
    """
    summary = {"checked": 0, "findings": 0, "skills": []}
    try:
        from agent_friday.skillopt_engine import get_engine, maybe_autoresearch
        engine = get_engine()
        for name in engine.list_skills():
            summary["checked"] += 1
            try:
                finding = maybe_autoresearch(name)
                if finding:
                    summary["findings"] += 1
                    summary["skills"].append(name)
            except Exception:
                continue
    except Exception as e:
        summary["error"] = str(e)
    return summary


def trajectory_stats(limit=1000):
    """Lightweight stats over recent trajectories (for dashboards/tests).

    F75 correction: "success" here means CONFIRMED success (score ==
    SUCCESS_SCORE) -- reachable by nothing today, since _success_score()
    has no real completion-evidence check yet (see its own docstring/
    follow-up note). Previously this summed every truthy score, which
    silently folded "unverified" (the old 1.0-for-everything-plausible
    default) into "success" -- exactly the false positive described above.
    Reports the 3-way breakdown explicitly instead. Records written before
    the three-state scoring still carry the old binary 0.0/1.0 scores, so
    historic "success" counts reflect the old, less honest meaning -- they
    are not retroactively reprocessed.
    """
    recs = []
    try:
        if TRAJ_FILE.exists():
            with open(TRAJ_FILE, encoding="utf-8") as f:
                for line in f.readlines()[-limit:]:
                    try:
                        recs.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        pass
    n = len(recs)
    succ = sum(1 for r in recs if r.get("success") == SUCCESS_SCORE)
    unverified = sum(1 for r in recs if r.get("success") == UNVERIFIED_SCORE)
    fail = sum(1 for r in recs if r.get("success") == FAILURE_SCORE)
    return {
        "count": n,
        "success": succ,
        "unverified": unverified,
        "failure": fail,
        "success_rate": round(succ / n, 3) if n else 0.0,
    }
