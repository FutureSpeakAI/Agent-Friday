"""The two endpoints behind `privacy/cloud_consent.py`'s explicit choice.

Deliberately NOT registered as an agent tool anywhere in this codebase —
grep `tool_registry`/`tool_list`/the MCP tool-schema builders for this
module's name and it will not appear. A model directing its own consent
decision is the exact shape of bug `enterprise_consent_grant` was removed
for ("a tool that let Friday's own AI grant itself permission to send data
to the cloud"). This surface exists
for the browser UI a human is looking at, and nowhere else.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from agent_friday.privacy import cloud_consent

privacy_consent_bp = Blueprint("privacy_consent", __name__)


@privacy_consent_bp.route("/api/privacy/cloud-consent", methods=["GET"])
def get_cloud_consent_status():
    """Whether the explicit choice has been made yet, and — only while it
    has not — this machine's own capability assessment, so the UI can
    decide which of the two screens to show without a second round trip.
    """
    try:
        payload = cloud_consent.status()
        # `?assess=1` is the Model Soup card's "Re-answer" control
        # (model-soup.md §10.2 step 5): a fresh capability assessment of the
        # machine as it is now, so the question can be asked again against a
        # measured local seat rather than the snapshot that said local was
        # incapable because nothing had been measured. Read-only; the answer
        # is still recorded only through POST.
        if request.args.get("assess") and "capability" not in payload:
            payload["capability"] = cloud_consent.assess_local_capability()
        return jsonify({"status": "ok", **payload})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@privacy_consent_bp.route("/api/privacy/cloud-consent", methods=["POST"])
def post_cloud_consent():
    """Record the explicit choice. The only legitimate write path — see
    `core._save_settings`'s docstring on `_internal_cloud_consent_write`
    and `cloud_consent.record_consent`'s own docstring for why a client's
    claim about its own hardware is never trusted here.
    """
    data = request.get_json(silent=True) or {}
    choice = data.get("choice")
    if choice not in cloud_consent.VALID_CHOICES:
        return jsonify({"status": "error",
                        "message": "choice must be one of %r"
                        % (cloud_consent.VALID_CHOICES,)}), 400
    try:
        record = cloud_consent.record_consent(choice)
    except cloud_consent.ConsentRejected as e:
        return jsonify({"status": "error", "message": str(e)}), 409
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "ok", **record})
