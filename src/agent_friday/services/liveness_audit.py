"""Output liveness — does this subsystem still earn its keep?

Stephen's own recommendation, 2026-08-17, and the through-line of the whole
day: nearly every defect found had the same signature — a subsystem reporting
healthy while producing nothing, or producing something nothing consumed.

  * the residency plan existed as JSON nothing read
  * no wiki-distillation job existed at all, so "it stopped working" had no
    mechanism to have stopped
  * Lyria was in the tool list and absent from the installed SDK
  * the Arbiter was written and nothing booted it
  * memory-dreaming ran nightly, on schedule, green, and consolidated **0**
    durable facts from 215 turns
  * session_summary completed in 291 seconds and wrote zero bytes
  * the trust graph, people graph, user model and learning loop have no files
  * the image progress bar had two values
  * cancel reported success and cancelled nothing

Every one of those passed a "did it run?" check. Not one of them would pass
"did it produce anything, and does anything read it?"

So this asks three questions, in order, and the second and third are the ones
that matter:

  1. **RAN**      — has it executed recently, per its own schedule?
  2. **PRODUCED** — is its most recent output non-empty and non-trivial?
  3. **CONSUMED** — does anything actually read that output?

A subsystem failing (1) is off or broken and usually visible. A subsystem
failing (2) or (3) looks perfectly healthy from every dashboard in this
codebase, and is the failure mode this repo is prone to. Those are reported
loudly rather than assumed fine.

Nothing here writes, runs a job, or repairs anything. It is a mirror.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

_log = logging.getLogger("friday.liveness")

HOME = Path(os.path.expanduser("~"))
FRIDAY_DIR = HOME / ".friday"

OK, STALE, EMPTY, ORPHANED, MISSING = "ok", "stale", "empty", "orphaned", "missing"

# The verdicts that mean "this looked fine and was not".
QUIET_FAILURES = (EMPTY, ORPHANED)


def _age_days(ts) -> float | None:
    if not ts:
        return None
    try:
        return (time.time() - float(ts)) / 86400.0
    except Exception:
        return None


def _newest_mtime(path: Path, pattern: str = "*"):
    try:
        if path.is_file():
            return path.stat().st_mtime
        files = [p for p in path.rglob(pattern) if p.is_file()]
        return max((p.stat().st_mtime for p in files), default=None)
    except Exception:
        return None


def _result(name, *, tier, status, ran=None, produced=None, consumed=None,
            detail="", consumer=None):
    return {
        "name": name, "tier": tier, "status": status,
        "ran": ran, "produced": produced, "consumed": consumed,
        "consumer": consumer, "detail": detail,
    }


# ── Probes ──────────────────────────────────────────────────────────────────
#
# Each probe answers the three questions for one subsystem. `consumed` is
# deliberately a STATIC claim about the code — "who reads this" is a property
# of the codebase, not something to infer at runtime — and it is written down
# here so that a subsystem whose only consumer is a display endpoint says so.

def _probe_conversation_memory():
    db = FRIDAY_DIR / "memory" / "conversations" / "chroma.sqlite3"
    if not db.exists():
        return _result("conversation memory (vectors)", tier="memory",
                       status=MISSING, ran=False, produced=False,
                       detail="no chroma store on disk")
    rows, newest, dim, model = 0, None, None, None
    try:
        c = sqlite3.connect(str(db))
        rows = c.execute("select count(*) from embeddings").fetchone()[0]
        try:
            newest = c.execute("select max(created_at) from embeddings").fetchone()[0]
        except Exception:
            newest = None
        for k, sv, iv in c.execute(
                "select key, str_value, int_value from collection_metadata"):
            if k == "embedding_model":
                model = sv
            elif k == "embedding_dim":
                dim = iv
        c.close()
    except Exception as e:
        return _result("conversation memory (vectors)", tier="memory",
                       status=EMPTY, detail="unreadable: %s" % e)
    fresh = True
    if newest:
        try:
            fresh = (datetime.now() - datetime.fromisoformat(str(newest))) < timedelta(days=3)
        except Exception:
            fresh = True
    status = OK if (rows and fresh) else (EMPTY if not rows else STALE)
    return _result(
        "conversation memory (vectors)", tier="memory", status=status,
        ran=bool(newest), produced=bool(rows), consumed=True,
        consumer="model_router recall → chat context",
        detail="%d vectors, newest %s%s" % (
            rows, newest or "unknown",
            ", %s @ %sd" % (model, dim) if model else ""))


def _probe_memory_dreaming():
    d = FRIDAY_DIR / "dreams"
    newest = _newest_mtime(d, "*.md")
    if newest is None:
        return _result("memory dreaming (consolidation)", tier="memory",
                       status=MISSING, ran=False, produced=False,
                       detail="no consolidation output has ever been written")
    facts, reviewed, latest = 0, 0, None
    try:
        files = sorted([p for p in d.glob("*.md")],
                       key=lambda p: p.stat().st_mtime, reverse=True)
        latest = files[0]
        txt = latest.read_text(encoding="utf-8", errors="replace")
        import re
        m = re.search(r"Consolidated (\d+) durable fact", txt)
        facts = int(m.group(1)) if m else 0
        m2 = re.search(r"Reviewed (\d+) turns", txt)
        reviewed = int(m2.group(1)) if m2 else 0
    except Exception:
        pass
    ran_recently = _age_days(newest) is not None and _age_days(newest) < 2
    # THE case this whole module exists for: it ran, on time, and consolidated
    # nothing out of a busy day.
    produced = facts > 0 or reviewed == 0
    status = OK if (ran_recently and produced) else (
        EMPTY if ran_recently else STALE)
    return _result(
        "memory dreaming (consolidation)", tier="memory", status=status,
        ran=ran_recently, produced=facts > 0, consumed=False,
        consumer="/api/memory/dreams (display only) — nothing feeds it back "
                 "into Friday's context",
        detail="last run %s: reviewed %d turns, consolidated %d fact(s)" % (
            latest.name if latest else "?", reviewed, facts))


def _probe_wiki():
    w = FRIDAY_DIR / "wiki"
    pages = [p for p in w.rglob("*.md") if p.name != "_index.md"] if w.exists() else []
    newest = max((p.stat().st_mtime for p in pages), default=None)
    pend = FRIDAY_DIR / "wiki-pending.json"
    pending = 0
    try:
        items = json.loads(pend.read_text(encoding="utf-8"))
        pending = sum(1 for i in items if i.get("status") == "pending")
    except Exception:
        pass
    age = _age_days(newest)
    status = OK if (age is not None and age < 7) else STALE
    return _result(
        "wiki (durable knowledge)", tier="memory", status=status,
        ran=bool(pages), produced=bool(pages), consumed=True,
        consumer="system prompt wiki context (smart routing)",
        detail="%d pages, newest %s, %d proposal(s) awaiting approval" % (
            len(pages),
            datetime.fromtimestamp(newest).strftime("%Y-%m-%d") if newest else "never",
            pending))


def _probe_session_summaries():
    d = FRIDAY_DIR / "session_summaries"
    newest = _newest_mtime(d)
    if newest is None:
        return _result(
            "session summaries (continuity)", tier="memory", status=MISSING,
            ran=None, produced=False, consumed=True,
            consumer="_build_session_continuity_block → system prompt",
            detail="the daily job completes, and no summary file has ever "
                   "appeared — measured 291s of local inference for zero bytes")
    age = _age_days(newest)
    return _result(
        "session summaries (continuity)", tier="memory",
        status=OK if (age or 99) < 3 else STALE,
        ran=True, produced=True, consumed=True,
        consumer="_build_session_continuity_block → system prompt",
        detail="newest %s" % datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M"))


def _probe_file_tier(label, path, consumer, pattern="*"):
    newest = _newest_mtime(Path(path), pattern)
    if newest is None:
        return _result(label, tier="memory", status=MISSING, ran=False,
                       produced=False, consumed=False, consumer=consumer,
                       detail="no file on disk — designed, never populated")
    age = _age_days(newest)
    return _result(
        label, tier="memory", status=OK if (age or 999) < 30 else STALE,
        ran=True, produced=True, consumed=True, consumer=consumer,
        detail="newest %s (%.0f days ago)" % (
            datetime.fromtimestamp(newest).strftime("%Y-%m-%d"), age or 0))


def _probe_schedules():
    """Every scheduled job: ran, produced, and whether its output is empty.

    `last_status: complete` with `last_summary: ""` is the exact shape that
    hid two broken jobs for days, so an empty summary from a completed run is
    reported as EMPTY, not OK.
    """
    out = []
    try:
        recs = json.loads((FRIDAY_DIR / "schedules.json").read_text(encoding="utf-8"))
    except Exception as e:
        return [_result("scheduler", tier="jobs", status=MISSING,
                        detail="schedules.json unreadable: %s" % e)]
    for j in recs:
        if not j.get("enabled"):
            continue
        name = j.get("name") or j.get("id")
        last_ts = j.get("last_run_ts")
        age = _age_days(last_ts)
        summary = (j.get("last_summary") or "").strip()
        status_ok = (j.get("last_status") == "complete")
        expected = {"interval": 1, "daily": 2, "weekly": 9}.get(j.get("trigger"), 3)
        ran = age is not None and age <= expected
        if age is None:
            st, detail = STALE, "has never run"
        elif not ran:
            st, detail = STALE, "last ran %.1f days ago (expects ~%dd)" % (age, expected)
        elif status_ok and not summary:
            st, detail = EMPTY, "completes and reports nothing — an empty " \
                                "summary from a green run is how a dead job hides"
        elif not status_ok:
            st, detail = EMPTY, "last status: %s" % j.get("last_status")
        else:
            st, detail = OK, summary[:100]
        out.append(_result(name, tier="jobs", status=st, ran=ran,
                           produced=bool(summary), consumed=None, detail=detail))
    return out


def _probe_capabilities():
    """Advertised capabilities that do not exist. Lyria is the standing case."""
    out = []
    try:
        from agent_friday.services.self_account import capabilities
        for c in capabilities():
            out.append(_result(
                c["name"], tier="capabilities",
                status=OK if c.get("available") else MISSING,
                ran=None, produced=c.get("available"), consumed=None,
                detail=(c.get("note") or c.get("how") or "")[:160]))
    except Exception as e:
        out.append(_result("capabilities", tier="capabilities", status=MISSING,
                           detail="probe failed: %s" % e))
    return out


def _probe_embedder():
    """Which embedder is REALLY producing vectors, measured not configured."""
    try:
        from agent_friday import conversation_memory as cm
        store = cm.ConversationMemory()
        dim = store._current_dimension()
        return _result(
            "embedder", tier="memory",
            status=OK if dim else EMPTY, ran=True, produced=bool(dim),
            consumed=True, consumer="conversation memory + knowledge graph",
            detail="%s at %s dimensions (measured)" % (
                getattr(store, "model_name", "?"), dim))
    except Exception as e:
        return _result("embedder", tier="memory", status=EMPTY,
                       detail="could not measure: %s" % e)


def _probe_silent_work():
    """Work that ran and reported NOTHING about itself.

    Stephen has opened the notifications dropdown on five separate occasions,
    on a process that was genuinely running, and seen "— waiting for activity —"
    every time. Nothing in this codebase treated that as a fault: the task was
    running, so every health view said fine.

    A unit of work that produces zero log lines and zero steps is not busy —
    it is invisible, and invisible is the condition he has been staring at all
    day. It gets reported here as the anomaly it is.
    """
    out = []
    try:
        from agent_friday.core import PROCESSES, PROCESSES_LOCK
        with PROCESSES_LOCK:
            rows = [dict(p, id=k) for k, p in PROCESSES.items()]
    except Exception:
        return []
    silent = []
    for p in rows:
        if p.get("status") != "running":
            continue
        if (p.get("category") or "default") == "default":
            continue
        started = p.get("started") or time.time()
        if time.time() - started < 20:      # give it a moment to say something
            continue
        if not (p.get("log") or p.get("steps")):
            silent.append(p)
    for p in silent:
        out.append(_result(
            "silent work: %s" % (p.get("label") or p.get("name") or p.get("id")),
            tier="observability", status=EMPTY, ran=True, produced=False,
            consumed=False,
            consumer="notifications dropdown / orb thread panel",
            detail="running %ds with zero log lines and zero steps — this is "
                   "what renders as '— waiting for activity —'"
                   % int(time.time() - (p.get("started") or time.time()))))
    return out


def _probe_seat_drift():
    """Seats the PLAN calls resident against what is measurably serving.

    Added 2026-08-17 after I made this exact mistake in front of Stephen. The
    residency plan reports the embedder seat as `status: resident`, and I read
    that field and told him qwen3-embed was "loaded and idle, holding RAM".
    It is not loaded. It has never been started: there is no entry for it in
    runtime/residency/endpoints.json, no llama-server process serving it, and
    no qwen gguf in Friday's model store at all — the weights exist only in
    Ollama's store.

    `status` in the plan is a DECLARATION. An endpoint is EVIDENCE. Wherever
    those two disagree, the declaration is what fooled somebody, so this
    reports the disagreement rather than either half.
    """
    out = []
    try:
        from agent_friday.services.residency_arbiter import get_arbiter, owned_endpoint
        arb = get_arbiter()
        if arb is None:
            return []
        seats = (arb.plan or {}).get("seats") or {}
    except Exception:
        return []
    for role, s in seats.items():
        if not isinstance(s, dict) or not s.get("model_id"):
            continue
        claimed = str(s.get("status") or "")
        try:
            live = bool(owned_endpoint(s["model_id"]))
        except Exception:
            live = False
        if claimed in ("resident", "pinned") and not live:
            out.append(_result(
                "seat: %s" % role, tier="residency", status=ORPHANED,
                ran=False, produced=False, consumed=False,
                consumer="dispatch resolves seats through endpoints.json",
                detail="plan says '%s' for %s — no endpoint, nothing serving it"
                       % (claimed, s["model_id"])))
    return out


def audit() -> dict:
    """Run every probe. Returns {generated_at, summary, findings[]}."""
    findings = []
    for fn in (_probe_embedder, _probe_conversation_memory, _probe_memory_dreaming,
               _probe_wiki, _probe_session_summaries):
        try:
            findings.append(fn())
        except Exception as e:
            _log.warning("liveness probe failed: %s", e)
    for label, path, consumer in (
        ("trust graph", FRIDAY_DIR / "trust", "trust scoring in chat"),
        ("people graph", FRIDAY_DIR / "people_graph.json", "contact resolution"),
        ("user model", FRIDAY_DIR / "user_model.json", "system prompt personalization"),
        ("learning loop", FRIDAY_DIR / "learning", "learned heuristics block"),
        ("cognitive memory", FRIDAY_DIR / "memory.json", "legacy recall"),
    ):
        try:
            findings.append(_probe_file_tier(label, path, consumer))
        except Exception:
            pass
    try:
        findings += _probe_silent_work()
    except Exception as e:
        _log.warning("silent-work probe failed: %s", e)
    try:
        findings += _probe_seat_drift()
    except Exception as e:
        _log.warning("seat drift probe failed: %s", e)
    try:
        findings += _probe_schedules()
    except Exception as e:
        _log.warning("schedule probe failed: %s", e)
    try:
        findings += _probe_capabilities()
    except Exception:
        pass

    quiet = [f for f in findings if f["status"] in QUIET_FAILURES]
    missing = [f for f in findings if f["status"] == MISSING]
    stale = [f for f in findings if f["status"] == STALE]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total": len(findings),
            "ok": sum(1 for f in findings if f["status"] == OK),
            "quiet_failures": len(quiet),   # ran, looked fine, produced nothing
            "missing": len(missing),
            "stale": len(stale),
        },
        "headline": ("%d subsystem(s) are running and producing nothing"
                     % len(quiet)) if quiet else "nothing is quietly idle",
        "findings": sorted(findings, key=lambda f: (
            {EMPTY: 0, ORPHANED: 1, MISSING: 2, STALE: 3, OK: 4}.get(f["status"], 5),
            f["name"])),
    }


def render_text(report: dict | None = None) -> str:
    """The one page Stephen glances at."""
    r = report or audit()
    s = r["summary"]
    lines = ["OUTPUT LIVENESS — %s" % r["generated_at"],
             r["headline"].upper(),
             "%d checked · %d ok · %d producing nothing · %d missing · %d stale"
             % (s["total"], s["ok"], s["quiet_failures"], s["missing"], s["stale"]),
             ""]
    icon = {OK: "  ok  ", EMPTY: " EMPTY", ORPHANED: " ORPHAN", MISSING: "MISSING",
            STALE: " stale"}
    for f in r["findings"]:
        lines.append("%s  %-34s %s" % (icon.get(f["status"], "  ?   "),
                                       f["name"][:34], f["detail"][:90]))
        if f["status"] in QUIET_FAILURES and f.get("consumer"):
            lines.append("           consumer: %s" % f["consumer"][:88])
    return "\n".join(lines)
