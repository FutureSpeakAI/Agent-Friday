"""Deep research + privacy-gate transparency API.

  POST /api/research/propose      — commission + the protection plan up front
  POST /api/research/<id>/run     — start it (background)
  GET  /api/research/<id>         — live status; the orb reads THIS structure,
                                    so the UI can never invent progress
  GET  /api/research              — recent commissions
  GET  /api/research/<id>/report  — the styled page

  GET  /api/privacy/gate          — is the judgment layer actually running?
  GET  /api/privacy/left-the-machine — one row per cloud call: what travelled
  GET  /api/gpu/headroom          — does the desktop still have VRAM?

The privacy endpoints exist because a protection layer that is switched off is
indistinguishable, from the outside, from one that is switched on and working.
That is the "green but doing nothing" pattern, applied to the component where
it would matter most, so its state is reportable rather than inferable.
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

research_bp = Blueprint("research", __name__)


# ── research ──────────────────────────────────────────────────────────────────

@research_bp.route("/api/research/propose", methods=["POST"])
def api_research_propose():
    from agent_friday.services import research
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400
    try:
        return jsonify(research.propose(
            question, context=body.get("context"),
            disposition=body.get("disposition") or "now_local",
            budget=body.get("budget")))
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@research_bp.route("/api/research/<cid>/run", methods=["POST"])
def api_research_run(cid):
    from agent_friday.services import research
    from agent_friday.services.research.objects import Commission
    c = Commission.load(cid)
    if c is None:
        return jsonify({"error": "no such commission"}), 404
    # RS3: the label may not lie. A commission the gate refused cloud for
    # cannot be started with a cloud disposition, whatever the caller asks.
    disp = (request.get_json(silent=True) or {}).get("disposition")
    if disp:
        if disp == "now_cloud" and not c.protection.cloud_allowed:
            return jsonify({"error": "this commission cannot use a cloud model",
                            "reason": c.protection.sentence()}), 400
        c.disposition = disp
        c.save()
    research.run_async(cid)
    return jsonify({"started": True, "commission_id": cid,
                    "status_url": f"/api/research/{cid}"})


@research_bp.route("/api/research/<cid>")
def api_research_status(cid):
    from agent_friday.services import research
    out = research.status(cid)
    return (jsonify(out), 404) if out.get("error") else jsonify(out)


@research_bp.route("/api/research")
def api_research_list():
    from agent_friday.services.research.objects import list_commissions
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    return jsonify({"commissions": list_commissions(limit)})


@research_bp.route("/api/research/<cid>/report")
def api_research_report(cid):
    from agent_friday.services.research.objects import Commission
    c = Commission.load(cid)
    if c is None or not c.styled_path or not Path(c.styled_path).exists():
        return jsonify({"error": "no styled report for this commission"}), 404
    return send_file(c.styled_path, mimetype="text/html")


# ── privacy transparency ──────────────────────────────────────────────────────

@research_bp.route("/api/privacy/gate")
def api_privacy_gate():
    """Is the judgment layer on, and did its probes pass?"""
    from agent_friday.services import judgment_gate as jg
    try:
        return jsonify(jg.state())
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@research_bp.route("/api/privacy/left-the-machine")
def api_left_the_machine():
    """§5.8 — one row per cloud call: when, where, what verdicts, what kinds
    were scrubbed, whether judgment overturned the keyword layer, and why."""
    from agent_friday.core import FRIDAY_DIR
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    path = Path(FRIDAY_DIR) / "vault" / "egress-log.jsonl"
    rows = []
    try:
        for ln in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
    except Exception:
        rows = []
    from agent_friday.services import judgment_gate as jg
    return jsonify({
        "rows": list(reversed(rows)),
        "overturns": jg.read_overturns(limit),
        "digest_7d": jg.overturn_digest(7),
        "gate": jg.state(),
    })


@research_bp.route("/api/research/search-backend")
def api_search_backend():
    """Which search backend is live, and is the Brave key found?

    A paid key that went into the wrong store would otherwise be silently
    ignored, with search staying on the scrape and nothing saying why.
    """
    from agent_friday.services import firecrawl, web_search
    out = web_search.key_status()
    out["canary"] = web_search.canary()
    # The chain, in the order it is actually tried, each with its own state.
    out["chain"] = [
        {"backend": "firecrawl", "role": "primary",
         "health": web_search.firecrawl_health()},
        {"backend": "brave", "role": "fallback",
         "health": web_search.health_state()},
        {"backend": "duckduckgo-scrape", "role": "last resort",
         "health": {"state": "scrape",
                    "detail": "no key; fragile, and has already broken once"}},
    ]
    out["active"] = web_search.active_backend()
    if request.args.get("verify") == "1":
        # Live entitlement checks — these spend real requests, so opt-in.
        out["verify"] = {"brave": web_search.verify_key(),
                         "firecrawl": firecrawl.verify()}
    return jsonify(out)


@research_bp.route("/api/gpu/headroom")
def api_gpu_headroom():
    """Does the desktop still have VRAM? Stephen lost a monitor to this."""
    from agent_friday.services import gpu_headroom
    return jsonify({"gpus": gpu_headroom.gpu_memory(),
                    "display": gpu_headroom.display_at_risk()})
