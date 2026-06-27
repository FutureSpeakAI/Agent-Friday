"""
Agent Friday — Ownership API Routes (Layer 2)
FutureSpeak.AI · Asimov's Mind

/api/ownership/* endpoints:
  POST  /api/ownership/register                  register an artifact + build manifest
  GET   /api/ownership/asset/<id>                full asset record + embedded manifest
  GET   /api/ownership/by-creator                list assets for a creator pubkey
  GET   /api/ownership/all                       paginated list of all assets
  POST  /api/ownership/transfer                  record a signed ownership transfer
  GET   /api/ownership/transfers/<id>            transfer history for an asset
  GET   /api/ownership/provenance-chain/<id>     walk the sources DAG to roots
  POST  /api/ownership/verify                    full integrity verification
  POST  /api/ownership/license-check             license compatibility check
  GET   /api/ownership/manifest/<content_hash>   raw provenance sidecar lookup
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import ownership, provenance as pv

ownership_bp = Blueprint("ownership", __name__)


@ownership_bp.route("/api/ownership/register", methods=["POST"])
@login_required
def register_asset():
    data = request.get_json(silent=True) or {}
    file_path = data.get("file_path")
    if not file_path:
        return jsonify({"error": "file_path required"}), 400
    rec = ownership.register(
        file_path,
        manifest=data.get("manifest"),
        title=data.get("title"),
        auto_build=bool(data.get("auto_build", True)),
    )
    if not rec:
        return jsonify({"error": "registration failed"}), 500
    return jsonify({"ok": True, "asset": rec})


@ownership_bp.route("/api/ownership/asset/<asset_id>", methods=["GET"])
@login_required
def get_asset(asset_id):
    rec = ownership.get_asset(asset_id)
    if not rec:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True, "asset": rec})


@ownership_bp.route("/api/ownership/by-creator", methods=["GET"])
@login_required
def list_by_creator():
    pubkey = request.args.get("creator_pubkey")
    assets = ownership.list_by_creator(pubkey)
    return jsonify({"ok": True, "assets": assets, "count": len(assets)})


@ownership_bp.route("/api/ownership/all", methods=["GET"])
@login_required
def list_all():
    limit = min(int(request.args.get("limit", 100)), 500)
    offset = int(request.args.get("offset", 0))
    assets = ownership.list_all(limit=limit, offset=offset)
    return jsonify({"ok": True, "assets": assets, "count": len(assets)})


@ownership_bp.route("/api/ownership/transfer", methods=["POST"])
@login_required
def transfer_asset():
    data = request.get_json(silent=True) or {}
    asset_id = data.get("asset_id")
    to_key = data.get("to_key")
    signature = data.get("signature")
    if not (asset_id and to_key and signature):
        return jsonify({"error": "asset_id, to_key, and signature required"}), 400
    rec = ownership.transfer(
        asset_id, to_key, signature,
        from_key=data.get("from_key"),
    )
    if not rec:
        return jsonify({"error": "transfer failed — asset not found or DB error"}), 500
    return jsonify({"ok": True, "transfer": rec})


@ownership_bp.route("/api/ownership/transfers/<asset_id>", methods=["GET"])
@login_required
def get_transfers(asset_id):
    records = ownership.get_transfers(asset_id)
    return jsonify({"ok": True, "transfers": records, "count": len(records)})


@ownership_bp.route("/api/ownership/provenance-chain/<asset_id>", methods=["GET"])
@login_required
def provenance_chain(asset_id):
    chain = ownership.provenance_chain(asset_id)
    return jsonify({"ok": True, "chain": chain, "length": len(chain)})


@ownership_bp.route("/api/ownership/verify", methods=["POST"])
@login_required
def verify_asset():
    data = request.get_json(silent=True) or {}
    target = data.get("asset_id") or data.get("file_path")
    if not target:
        return jsonify({"error": "asset_id or file_path required"}), 400
    result = ownership.verify(target)
    return jsonify({"ok": True, **result})


@ownership_bp.route("/api/ownership/license-check", methods=["POST"])
@login_required
def license_check():
    data = request.get_json(silent=True) or {}
    source_id = data.get("source_asset_id")
    deriv_license = data.get("derivative_license")
    if not (source_id and deriv_license):
        return jsonify({"error": "source_asset_id and derivative_license required"}), 400
    result = ownership.check_license_compat(
        source_id, deriv_license,
        requestor_pubkey=data.get("requestor_pubkey"),
    )
    return jsonify({"ok": True, **result})


@ownership_bp.route("/api/ownership/manifest/<content_hash>", methods=["GET"])
@login_required
def get_manifest(content_hash):
    manifest = pv.get_manifest(content_hash)
    if not manifest:
        return jsonify({"error": "manifest not found"}), 404
    return jsonify({"ok": True, "manifest": manifest})
