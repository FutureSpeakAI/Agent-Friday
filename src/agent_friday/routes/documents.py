"""routes/documents.py — the owner's signing materials for PDF signing.

  GET    /api/documents/signing/status        what is set (never the secrets)
  POST   /api/documents/signing/image         {"image_b64": "..."}  (PNG/JPEG, data: URL ok)
  DELETE /api/documents/signing/image
  POST   /api/documents/signing/certificate   {"certificate_b64": "...", "passphrase": "..."}
  DELETE /api/documents/signing/certificate

Every route is LOCAL ONLY, on top of the app-wide login gate: a signature and
a signing key are set by the owner at their own machine. Writes need a
same-origin JSON body. Secrets go in and never come back out.

Importing this module registers the approval hook that signs an approved
card (services/pdf_signing); route modules are imported on every boot.
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

from agent_friday.core import _is_local_request, login_required
from agent_friday.services import pdf_signing

documents_bp = Blueprint("documents", __name__)


def _guard(write: bool = True):
    if not _is_local_request():
        return jsonify({"ok": False, "error": "signing settings can only be changed "
                                              "on Friday's own computer"}), 403
    if write:
        if not request.is_json:
            return jsonify({"ok": False, "error": "JSON body required"}), 415
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            return jsonify({"ok": False, "error": "cross-origin request refused"}), 403
    return None


@documents_bp.route("/api/documents/signing/status", methods=["GET"])
@login_required
def signing_status():
    bad = _guard(write=False)
    if bad:
        return bad
    return jsonify({"ok": True, **pdf_signing.status()})


@documents_bp.route("/api/documents/signing/image", methods=["POST"])
@login_required
def signing_image_set():
    bad = _guard()
    if bad:
        return bad
    body = request.get_json(silent=True) or {}
    try:
        pdf_signing.set_signature_image(pdf_signing.decode_b64(body.get("image_b64")))
    except pdf_signing.SignRefused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **pdf_signing.status()})


@documents_bp.route("/api/documents/signing/image", methods=["DELETE"])
@login_required
def signing_image_clear():
    bad = _guard(write=False)
    if bad:
        return bad
    pdf_signing.clear_signature_image()
    return jsonify({"ok": True, **pdf_signing.status()})


@documents_bp.route("/api/documents/signing/certificate", methods=["POST"])
@login_required
def signing_certificate_set():
    bad = _guard()
    if bad:
        return bad
    body = request.get_json(silent=True) or {}
    try:
        pdf_signing.set_certificate(pdf_signing.decode_b64(body.get("certificate_b64")),
                                    str(body.get("passphrase") or ""))
    except pdf_signing.SignRefused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **pdf_signing.status()})


@documents_bp.route("/api/documents/signing/certificate", methods=["DELETE"])
@login_required
def signing_certificate_clear():
    bad = _guard(write=False)
    if bad:
        return bad
    pdf_signing.clear_certificate()
    return jsonify({"ok": True, **pdf_signing.status()})
