"""
Agent Friday — Defederation & Content Policy Routes (Layer 3)
FutureSpeak.AI · Asimov's Mind

/api/defederation/*  — Asimov-governed defederation protocol
/api/policies/*      — Community content policy packs
"""
from flask import Blueprint, jsonify, request
from core import login_required
from services import defederation
from services import content_policies

defederation_bp = Blueprint("defederation", __name__)


# ─────────────────────────────────────────────────────────────────────────────
#  DEFEDERATION: Assessments
# ─────────────────────────────────────────────────────────────────────────────

@defederation_bp.route("/api/defederation/assessments", methods=["GET"])
@login_required
def list_assessments():
    """
    List defederation assessments.

    Query params:
      agent_pubkey    — assessments ABOUT this agent
      assessor_pubkey — assessments FROM this assessor
      active_only     — "false" to include withdrawn (default "true")
    """
    agent_pubkey    = request.args.get("agent_pubkey")
    assessor_pubkey = request.args.get("assessor_pubkey")
    active_only     = request.args.get("active_only", "true").lower() != "false"

    if agent_pubkey:
        items = defederation.get_assessments_for(agent_pubkey, active_only=active_only)
    elif assessor_pubkey:
        items = defederation.get_assessments_by(assessor_pubkey)
    else:
        return jsonify({"error": "agent_pubkey or assessor_pubkey required"}), 400

    return jsonify({"ok": True, "assessments": items, "count": len(items)})


@defederation_bp.route("/api/defederation/assess", methods=["POST"])
@login_required
def create_assessment():
    """
    Submit a new defederation assessment.

    Body:
      agent_pubkey   — the agent being assessed (required)
      evidence       — non-empty list of {content_hash, timestamp, violation_type}
      harm_category  — one of VALID_HARM_CATEGORIES
      severity_score — float 0.0-1.0
      recommendation — MONITOR | RESTRICT | DEFEDERATE
      reasoning      — explanation string
    """
    data = request.get_json(silent=True) or {}
    agent_pubkey   = data.get("agent_pubkey")
    evidence       = data.get("evidence")
    harm_category  = data.get("harm_category")
    severity_score = data.get("severity_score", 0.5)
    recommendation = data.get("recommendation")
    reasoning      = data.get("reasoning", "")

    if not agent_pubkey:
        return jsonify({"error": "agent_pubkey required"}), 400

    if not evidence or not isinstance(evidence, list) or len(evidence) == 0:
        return jsonify({
            "error": "evidence required — must be a non-empty list of "
                     "{content_hash, timestamp, violation_type}"
        }), 400

    if harm_category not in defederation.VALID_HARM_CATEGORIES:
        return jsonify({
            "error": "invalid harm_category",
            "valid_categories": sorted(defederation.VALID_HARM_CATEGORIES),
        }), 400

    if recommendation not in defederation.VALID_RECOMMENDATIONS:
        return jsonify({
            "error": "invalid recommendation",
            "valid_recommendations": sorted(defederation.VALID_RECOMMENDATIONS),
        }), 400

    assessment = defederation.create_assessment(
        agent_pubkey=agent_pubkey,
        evidence=evidence,
        harm_category=harm_category,
        severity_score=float(severity_score),
        recommendation=recommendation,
        reasoning=reasoning,
    )
    if not assessment:
        return jsonify({"error": "assessment creation failed"}), 500

    consensus = defederation.get_consensus(agent_pubkey)
    return jsonify({"ok": True, "assessment": assessment, "consensus": consensus})


@defederation_bp.route("/api/defederation/consensus/<path:agent_pubkey>", methods=["GET"])
@login_required
def get_consensus(agent_pubkey):
    """Return the current consensus verdict and active assessments for an agent."""
    consensus   = defederation.get_consensus(agent_pubkey)
    assessments = defederation.get_assessments_for(agent_pubkey, active_only=True)
    return jsonify({
        "ok": True,
        "agent_pubkey": agent_pubkey,
        "consensus": consensus,
        "assessments": assessments,
    })


@defederation_bp.route("/api/defederation/withdraw/<assessment_id>", methods=["POST"])
@login_required
def withdraw_assessment(assessment_id):
    """Withdraw an assessment (assessor only). Creates a signed withdrawal record."""
    data            = request.get_json(silent=True) or {}
    assessor_pubkey = data.get("assessor_pubkey", "")

    if not assessor_pubkey:
        try:
            from services import federation as fed
            assessor_pubkey = fed.get_identity().get("agent_id", "")
        except Exception:
            pass

    result = defederation.withdraw_assessment(assessment_id, assessor_pubkey)
    if not result:
        return jsonify({"error": "assessment not found or not authorized to withdraw"}), 404

    return jsonify({"ok": True, "assessment": result})


@defederation_bp.route("/api/defederation/patterns/<path:agent_pubkey>", methods=["GET"])
@login_required
def detect_patterns(agent_pubkey):
    """Run all local pattern detectors for an agent and return aggregated results."""
    return jsonify({
        "ok": True,
        "agent_pubkey": agent_pubkey,
        "patterns": {
            "harassment":            defederation.detect_harassment_pattern(agent_pubkey),
            "radicalization":        defederation.detect_radicalization_pattern(agent_pubkey),
            "epistemic_manipulation": defederation.detect_epistemic_manipulation(agent_pubkey),
        },
    })


@defederation_bp.route("/api/defederation/sockpuppet-check", methods=["POST"])
@login_required
def sockpuppet_check():
    """
    Check whether a group of agents appears to be a sockpuppet cluster.

    Body: {agent_pubkeys: [str, ...]}  — at least 2 keys required.
    """
    data = request.get_json(silent=True) or {}
    keys = data.get("agent_pubkeys") or []
    if len(keys) < 2:
        return jsonify({"error": "at least 2 agent_pubkeys required"}), 400
    result = defederation.detect_sockpuppet_cluster(keys)
    return jsonify({"ok": True, **result})


# ─────────────────────────────────────────────────────────────────────────────
#  CONTENT POLICIES: Pack Management
# ─────────────────────────────────────────────────────────────────────────────

@defederation_bp.route("/api/policies/available", methods=["GET"])
@login_required
def available_packs():
    """List all known packs with a `subscribed` boolean for the current agent."""
    packs         = content_policies.get_available_packs()
    subscribed_ids = {p["pack_id"] for p in content_policies.get_subscribed_packs()}
    for p in packs:
        p["subscribed"] = p["pack_id"] in subscribed_ids
    return jsonify({"ok": True, "packs": packs, "count": len(packs)})


@defederation_bp.route("/api/policies/subscribed", methods=["GET"])
@login_required
def subscribed_packs():
    """List all currently subscribed packs."""
    packs = content_policies.get_subscribed_packs()
    return jsonify({"ok": True, "packs": packs, "count": len(packs)})


@defederation_bp.route("/api/policies/subscribe", methods=["POST"])
@login_required
def subscribe_pack():
    """Subscribe to a content policy pack. Body: {pack_id: str}"""
    data    = request.get_json(silent=True) or {}
    pack_id = data.get("pack_id")
    if not pack_id:
        return jsonify({"error": "pack_id required"}), 400
    ok = content_policies.subscribe(pack_id)
    if not ok:
        return jsonify({"error": "pack not found or subscription failed"}), 400
    return jsonify({"ok": True, "pack_id": pack_id})


@defederation_bp.route("/api/policies/unsubscribe/<pack_id>", methods=["DELETE"])
@login_required
def unsubscribe_pack(pack_id):
    """Unsubscribe from a pack. Returns 403 for asimov-standard."""
    if pack_id == content_policies.ALWAYS_ON_PACK:
        return jsonify({
            "error": "cannot unsubscribe from asimov-standard — it is the cLaws minimum and always active"
        }), 403

    ok = content_policies.unsubscribe(pack_id)
    if not ok:
        return jsonify({"error": "pack not found or cannot be unsubscribed"}), 400
    return jsonify({"ok": True, "pack_id": pack_id})


@defederation_bp.route("/api/policies/create", methods=["POST"])
@login_required
def create_pack():
    """
    Create and publish a new content policy pack.

    Body:
      name        — human-readable name (required)
      description — what this pack does
      rules       — list of {category, action, severity_threshold, description}
      version     — semver string (default "1.0.0")
    """
    data        = request.get_json(silent=True) or {}
    name        = data.get("name")
    description = data.get("description", "")
    rules       = data.get("rules")

    if not name:
        return jsonify({"error": "name required"}), 400
    if not rules or not isinstance(rules, list):
        return jsonify({
            "error": "rules required — list of {category, action, severity_threshold, description}"
        }), 400

    pack = content_policies.create_pack(
        name=name,
        description=description,
        rules=rules,
        creator_pubkey=data.get("creator_pubkey"),
        version=data.get("version", "1.0.0"),
    )
    if not pack:
        return jsonify({"error": "pack creation failed — check rule format (action must be BLOCK/TAG/WARN/ALLOW)"}), 400

    return jsonify({"ok": True, "pack": pack}), 201


@defederation_bp.route("/api/policies/evaluate", methods=["POST"])
@login_required
def evaluate_content():
    """
    Evaluate content metadata against all subscribed packs.

    Body: content metadata dict — see content_policies.evaluate_content() for fields.
    Returns verdict with blocked, tags, warnings, blocking_rule.
    """
    data             = request.get_json(silent=True) or {}
    content_metadata = data.get("content") if "content" in data else data

    result = content_policies.evaluate_content(content_metadata)
    status = 422 if result.get("blocked") else 200
    return jsonify({"ok": not result.get("blocked"), **result}), status
