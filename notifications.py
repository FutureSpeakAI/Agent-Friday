"""
Notification templates for Agent Friday.

Generates Friday-Chat-ready notification payloads for:
    🔴 priority job alerts
    🟡 daily digests
    📊 weekly reports
    📞 interview detection
    🧠 skill improvement announcements

Every builder returns a dict with this shape:

    {
        "channel": "chat",
        "icon": "🔴" | ...,
        "priority": "high" | "normal" | "low",
        "title": str,
        "body": str,            # markdown, multi-line
        "summary": str,         # one-liner for OS notifications
        "actions": [            # optional CTAs
            {"label": "Apply", "kind": "apply_to_job", "payload": {...}}
        ],
        "meta": {"kind": "priority_job", "source": "job_scanner", ...},
    }

Server.py renders these into the chat stream + (optionally) the OS notification.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


# ── Icons ─────────────────────────────────────────────────────────────────

ICON_PRIORITY = "🔴"
ICON_DIGEST = "🟡"
ICON_REPORT = "📊"
ICON_INTERVIEW = "📞"
ICON_SKILL = "🧠"
ICON_OK = "🟢"
ICON_INFO = "💡"


# ── Helpers ───────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _salary_band(j: Dict[str, Any]) -> str:
    lo, hi = j.get("salary_min"), j.get("salary_max")
    if not lo and not hi:
        return ""
    lo_s = f"${lo // 1000}K" if lo else "?"
    hi_s = f"${hi // 1000}K" if hi else "?"
    return f"  ·  {lo_s}–{hi_s}"


def _location(j: Dict[str, Any]) -> str:
    if j.get("remote"):
        return "Remote"
    if j.get("hybrid"):
        return f"{j.get('location', '—')}  (hybrid)"
    return j.get("location") or "—"


def _job_one_liner(j: Dict[str, Any]) -> str:
    title = j.get("title") or "Untitled role"
    company = j.get("company") or "Unknown company"
    return f"**{title}** @ {company}  ·  {_location(j)}{_salary_band(j)}"


def _payload(channel: str, icon: str, priority: str, title: str, body: str,
             summary: str, meta: Dict[str, Any],
             actions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {
        "channel": channel,
        "icon": icon,
        "priority": priority,
        "title": title,
        "body": body.strip(),
        "summary": summary,
        "actions": actions or [],
        "meta": {"generated_at": _now_iso(), **meta},
    }


# ════════════════════════════════════════════════════════════════════════
#  Priority job alert  — 🔴
# ════════════════════════════════════════════════════════════════════════

def priority_job_alert(job: Dict[str, Any]) -> Dict[str, Any]:
    """High-priority single-job alert. Fires when score ≥ 0.80."""
    title = f"{ICON_PRIORITY} Priority job — {job.get('relevance_score', 0):.0%} match"
    body = (
        f"{_job_one_liner(job)}\n\n"
        f"**Why it surfaced:**\n"
    )
    breakdown = job.get("score_breakdown", {})
    for k, v in sorted(breakdown.items(), key=lambda x: -x[1]):
        body += f"  - `{k}` → {v:+.2f}\n"
    keywords = job.get("keywords_matched", [])
    if keywords:
        body += f"\n**Matched keywords:** {', '.join(keywords[:6])}\n"
    url = job.get("source_url")
    if url:
        body += f"\n[View posting]({url})\n"
    summary = f"Priority job: {job.get('title')} @ {job.get('company')}"
    return _payload(
        channel="chat",
        icon=ICON_PRIORITY,
        priority="high",
        title=title,
        body=body,
        summary=summary,
        meta={"kind": "priority_job", "source": "job_scanner",
              "job_id": job.get("job_id"), "score": job.get("relevance_score")},
        actions=[
            {"label": "Apply", "kind": "apply_to_job", "payload": {"job_id": job.get("job_id")}},
            {"label": "Open posting", "kind": "open_url", "payload": {"url": url}} if url else None,
            {"label": "Snooze", "kind": "snooze_job", "payload": {"job_id": job.get("job_id")}},
        ],
    )


# ════════════════════════════════════════════════════════════════════════
#  Daily digest  — 🟡
# ════════════════════════════════════════════════════════════════════════

def daily_digest(*, jobs_today: List[Dict[str, Any]],
                 applications_today: List[Dict[str, Any]],
                 responses_today: List[Dict[str, Any]]) -> Dict[str, Any]:
    """End-of-day rollup."""
    n_jobs = len(jobs_today)
    n_apps = len(applications_today)
    n_resp = len(responses_today)
    priority = [j for j in jobs_today if j.get("relevance_score", 0) >= 0.80]

    title = f"{ICON_DIGEST} Daily digest — {n_jobs} jobs scanned, {n_apps} applied"
    body = (
        f"**Today's activity**\n\n"
        f"  - Scanned: **{n_jobs}** new postings  "
        f"({len(priority)} priority)\n"
        f"  - Applied: **{n_apps}**\n"
        f"  - Responses: **{n_resp}**\n"
    )
    if priority:
        body += "\n**Top priority finds:**\n"
        for j in priority[:5]:
            body += f"  - {_job_one_liner(j)}  _(score {j.get('relevance_score', 0):.2f})_\n"
    if responses_today:
        body += "\n**Responses received:**\n"
        for r in responses_today[:5]:
            kind = r.get("response_kind", "update")
            body += f"  - {kind.title()} — {r.get('company', '—')} ({r.get('title', '—')})\n"
    if n_apps == 0 and n_jobs == 0:
        body += "\n_Quiet day on the job front._\n"
    summary = f"{n_jobs} new jobs · {n_apps} applied · {n_resp} responses"
    return _payload(
        channel="chat",
        icon=ICON_DIGEST,
        priority="normal",
        title=title,
        body=body,
        summary=summary,
        meta={"kind": "daily_digest", "source": "job_scanner",
              "counts": {"jobs": n_jobs, "applications": n_apps, "responses": n_resp}},
    )


# ════════════════════════════════════════════════════════════════════════
#  Weekly report  — 📊
# ════════════════════════════════════════════════════════════════════════

def weekly_report(*, pipeline_summary: Dict[str, int],
                  response_rate: Dict[str, Any],
                  top_jobs: List[Dict[str, Any]],
                  applications_this_week: int = 0,
                  interviews_this_week: int = 0) -> Dict[str, Any]:
    """7-day rollup with pipeline health metrics."""
    title = f"{ICON_REPORT} Weekly report — {applications_this_week} applications shipped"
    body = (
        f"**Pipeline snapshot**\n\n"
        f"  - Discovered: **{pipeline_summary.get('_total_jobs_discovered', 0)}** total  "
        f"({pipeline_summary.get('_priority_jobs_open', 0)} priority open)\n"
        f"  - Applied: **{pipeline_summary.get('applied', 0)}**\n"
        f"  - Screening: **{pipeline_summary.get('screening', 0)}**\n"
        f"  - Interview: **{pipeline_summary.get('interview', 0)}**\n"
        f"  - Offer: **{pipeline_summary.get('offer', 0)}**\n\n"
        f"**Response rate** _(last {response_rate.get('window_days', 30)} days)_\n\n"
        f"  - Response rate: **{response_rate.get('response_rate', 0):.0%}**\n"
        f"  - Interview rate: **{response_rate.get('interview_rate', 0):.0%}**\n"
        f"  - Offer rate: **{response_rate.get('offer_rate', 0):.0%}**\n"
        f"  - Ghost rate: **{response_rate.get('ghost_rate', 0):.0%}**\n"
    )
    if top_jobs:
        body += "\n**This week's standouts**\n\n"
        for j in top_jobs[:5]:
            body += f"  - {_job_one_liner(j)}  _(score {j.get('relevance_score', 0):.2f})_\n"
    summary = (f"{applications_this_week} applied · "
               f"{interviews_this_week} interviews · "
               f"response rate {response_rate.get('response_rate', 0):.0%}")
    return _payload(
        channel="chat",
        icon=ICON_REPORT,
        priority="normal",
        title=title,
        body=body,
        summary=summary,
        meta={"kind": "weekly_report", "source": "job_scanner",
              "pipeline_summary": pipeline_summary,
              "response_rate": response_rate},
    )


# ════════════════════════════════════════════════════════════════════════
#  Interview detection  — 📞
# ════════════════════════════════════════════════════════════════════════

def interview_detected(*, company: str, title: str, when: Optional[str] = None,
                       channel_detail: str = "", prep_link: Optional[str] = None,
                       application_id: Optional[str] = None) -> Dict[str, Any]:
    """Fires when an interview email/calendar event is detected."""
    when_str = f" on **{when}**" if when else ""
    title_line = f"{ICON_INTERVIEW} Interview detected — {company}"
    body = (
        f"**{title}** at **{company}**{when_str}\n\n"
        f"{channel_detail or '_(no additional details extracted)_'}\n\n"
        f"**Recommended prep:**\n"
        f"  - Re-read the original posting\n"
        f"  - Review prior application notes\n"
        f"  - Check Stephen's network for warm intros at this company\n"
        f"  - Generate company intel briefing (`brief {company}`)\n"
    )
    actions = [
        {"label": "Generate prep brief", "kind": "generate_brief",
         "payload": {"company": company, "title": title}},
    ]
    if prep_link:
        actions.append({"label": "Open prep doc", "kind": "open_url",
                        "payload": {"url": prep_link}})
    return _payload(
        channel="chat",
        icon=ICON_INTERVIEW,
        priority="high",
        title=title_line,
        body=body,
        summary=f"Interview: {title} @ {company}{when_str.replace('*', '')}",
        meta={"kind": "interview_detected", "source": "application_engine",
              "application_id": application_id,
              "company": company, "title": title},
        actions=actions,
    )


# ════════════════════════════════════════════════════════════════════════
#  Skill improvement announcement  — 🧠
# ════════════════════════════════════════════════════════════════════════

def skill_improvement(*, skill_name: str, new_version: str,
                       old_version: str, old_score: float, new_score: float,
                       edit_summary: str = "", edit_source: str = "autoresearch",
                       diff_preview: str = "") -> Dict[str, Any]:
    """Fires when SkillOpt promotes a new version."""
    pct = ((new_score - old_score) / max(old_score, 1e-6)) * 100.0
    title = f"{ICON_SKILL} Skill improved — {skill_name} (+{pct:.1f}%)"
    body = (
        f"**{skill_name}** was promoted from `{old_version}` to `{new_version}`.\n\n"
        f"  - Composite score: **{old_score:.3f} → {new_score:.3f}** ({pct:+.1f}%)\n"
        f"  - Edit source: `{edit_source}`\n"
        f"  - Summary: {edit_summary or '_(none)_'}\n"
    )
    if diff_preview:
        clipped = diff_preview[:1200]
        body += f"\n**Diff preview:**\n\n```diff\n{clipped}\n```\n"
    return _payload(
        channel="chat",
        icon=ICON_SKILL,
        priority="normal",
        title=title,
        body=body,
        summary=f"{skill_name}: {old_version} → {new_version} ({pct:+.1f}%)",
        meta={"kind": "skill_improvement", "source": "skillopt",
              "skill_name": skill_name,
              "old_version": old_version, "new_version": new_version,
              "old_score": old_score, "new_score": new_score},
        actions=[
            {"label": "Open in Observatory", "kind": "open_observatory",
             "payload": {"skill": skill_name}},
            {"label": "View diff", "kind": "view_skill_diff",
             "payload": {"skill": skill_name, "from": old_version, "to": new_version}},
        ],
    )


def skill_regression(*, skill_name: str, candidate_version: str,
                      candidate_score: float, best_score: float,
                      reason: str = "") -> Dict[str, Any]:
    """Fires when ValidationGate rejects a candidate — informational."""
    delta = ((candidate_score - best_score) / max(best_score, 1e-6)) * 100.0
    title = f"{ICON_INFO} Skill candidate rejected — {skill_name}"
    body = (
        f"**{skill_name}** candidate `{candidate_version}` was rejected by the "
        f"validation gate.\n\n"
        f"  - Candidate score: **{candidate_score:.3f}**  ({delta:+.1f}% vs best)\n"
        f"  - Reason: _{reason or 'regression vs current best'}_\n\n"
        f"_No change to the active version. Autoresearch will continue iterating._\n"
    )
    return _payload(
        channel="chat",
        icon=ICON_INFO,
        priority="low",
        title=title,
        body=body,
        summary=f"{skill_name}: {candidate_version} rejected ({delta:+.1f}%)",
        meta={"kind": "skill_regression", "source": "skillopt",
              "skill_name": skill_name,
              "candidate_version": candidate_version,
              "candidate_score": candidate_score, "best_score": best_score},
    )


# ════════════════════════════════════════════════════════════════════════
#  Convenience: format for an OS notification toast
# ════════════════════════════════════════════════════════════════════════

def to_os_toast(payload: Dict[str, Any]) -> Dict[str, str]:
    """Trim a chat payload into a flat title/body for OS-level notifications."""
    return {
        "title": payload.get("title", ""),
        "body": payload.get("summary", payload.get("title", "")),
        "icon": payload.get("icon", ICON_INFO),
        "priority": payload.get("priority", "normal"),
    }


__all__ = [
    "priority_job_alert",
    "daily_digest",
    "weekly_report",
    "interview_detected",
    "skill_improvement",
    "skill_regression",
    "to_os_toast",
]
