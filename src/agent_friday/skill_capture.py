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

import os
import json
import time
import threading
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
FRIDAY_DIR = HOME / ".friday"
TRAJ_FILE = FRIDAY_DIR / "trajectories.jsonl"

_LOCK = threading.Lock()
_MAX_TRAJ_KEEP = 5000

# Reply prefixes that mean the turn did NOT succeed.
_DENY_PREFIXES = ("[GOVERNANCE DENY]", "[SANDBOX DENY]", "Tool error", "[Friday offline]")


def _success_score(reply, error):
    if error:
        return 0.0
    r = (reply or "").strip()
    if len(r) < 8:
        return 0.0
    if r.startswith(_DENY_PREFIXES):
        return 0.0
    return 1.0


def _append_jsonl(rec):
    with _LOCK:
        try:
            FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
            with open(TRAJ_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            pass


def capture(message, reply, tool_trace=None, duration_ms=None, error=None, workspace=None):
    """Record one task trajectory and accumulate metrics on matched skills.

    Best-effort and silent — never raises into the chat path.
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
                        metrics={"quality": score, "success": score},
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
    """Lightweight stats over recent trajectories (for dashboards/tests)."""
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
    succ = sum(1 for r in recs if r.get("success"))
    return {
        "count": n,
        "success": succ,
        "success_rate": round(succ / n, 3) if n else 0.0,
    }
