"""Career pipeline endpoints â€” wires the bundled job_scanner and
application_engine skills (complete since v4.x but previously unreachable
from the web API) into Flask.

Safety posture:
  * /scan accepts pushed raw listings (the fetcher seam) â€” the default
    LinkedIn fetcher is a stub, so no scraping happens unless a real fetcher
    is configured upstream.
  * /apply defaults to dry_run=True; the default submitter never submits.
    Real submission requires an explicit {"dry_run": false} AND passing the
    engine's quality gates / confirmation thresholds.
  * Cover letters get an optional LLM polish via the configured provider and
    fall back to the engine's template drafter.

NOTE: this module deliberately uses explicit imports (no `from X import *`)
â€” it is the template for the codebase-wide conversion.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from data.job_tracker_schema import JobTracker
from skills.application_engine import engine as app_engine
from skills.job_scanner import scanner as job_scanner

jobs_bp = Blueprint("jobs", __name__)


def _notify_adapter(payload):
    """Bridge skill notifications into Friday's notification engine."""
    try:
        import agent_friday.notifications_engine as notif
        notif.push(
            title=str(payload.get("title") or "Career pipeline"),
            body=str(payload.get("body") or ""),
            priority=str(payload.get("priority") or "medium"),
            source="career", kind="career",
            meta=payload.get("meta") or {},
        )
    except Exception:
        pass


def _llm_cover_drafter(listing, resume_pack, config):
    """Polish the template cover letter with the configured LLM provider;
    fall back to the template on any failure (offline, no keys, etc.)."""
    base = app_engine._default_cover_drafter(listing, resume_pack, config)
    try:
        from agent_friday.services.model_router import _generate_text
        prompt = (
            "Rewrite the cover letter below for the role "
            f"'{listing.title}' at {listing.company}. Keep it under 220 words, "
            "specific and warm, no clichÃ©s, plain text only.\n\n"
            f"--- DRAFT ---\n{base}"
        )
        text = _generate_text(prompt)
        if isinstance(text, str) and text.strip():
            return text.strip()
    except Exception:
        pass
    return base


@jobs_bp.route("/api/pipeline/jobs", methods=["GET"])
def api_jobs_list():
    try:
        min_score = float(request.args.get("min_score", 0.0))
    except ValueError:
        min_score = 0.0
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        limit = 50
    tracker = JobTracker().load()
    jobs = tracker.list_jobs(min_score=min_score, limit=limit)
    return jsonify({"count": len(jobs), "jobs": [j.to_dict() for j in jobs]})


@jobs_bp.route("/api/pipeline/scan", methods=["POST"])
def api_jobs_scan():
    data = request.get_json(silent=True) or {}
    keywords = data.get("keywords")
    raw_listings = data.get("raw_listings")

    fetcher = None
    if isinstance(raw_listings, list) and raw_listings:
        # Push mode: caller (UI, MCP tool, private-side scraper) supplies the
        # raw listings; the engine still scores, dedupes, tracks, notifies.
        fetcher = lambda url: raw_listings  # noqa: E731

    result = job_scanner.scan(
        fetcher=fetcher,
        notify=_notify_adapter,
        keyword_set_override=keywords if isinstance(keywords, list) else None,
    )
    return jsonify(result)


@jobs_bp.route("/api/pipeline/jobs/<job_id>/apply", methods=["POST"])
def api_jobs_apply(job_id):
    data = request.get_json(silent=True) or {}
    tracker = JobTracker().load()
    if not tracker.get_job(job_id):
        return jsonify({"error": f"job {job_id} not found"}), 404

    result = app_engine.apply_to_job(
        job_id=job_id,
        tracker=tracker,
        dry_run=bool(data.get("dry_run", True)),
        force_confirm=bool(data.get("force_confirm", False)),
        resume_variant=data.get("resume_variant"),
        cover_drafter=_llm_cover_drafter,
        notifier=_notify_adapter,
    )
    return jsonify(result)


@jobs_bp.route("/api/pipeline/applications/<application_id>/response", methods=["POST"])
def api_jobs_record_response(application_id):
    data = request.get_json(silent=True) or {}
    kind = str(data.get("response_kind") or "").strip()
    if not kind:
        return jsonify({"error": "response_kind required"}), 400
    result = app_engine.record_response(
        application_id=application_id, response_kind=kind,
    )
    if not result.get("updated"):
        return jsonify(result), 404
    return jsonify(result)
