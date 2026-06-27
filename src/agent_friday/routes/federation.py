"""
Agent Friday — Federation, Marketplace, Economy & Moderation Routes (Layer 3)
FutureSpeak.AI · Asimov's Mind

/api/federation/*   — agent identity, peer discovery, encrypted messaging
/api/marketplace/*  — content listings, purchase flow
/api/economy/*      — wallet, transfers, leaderboard
/api/moderation/*   — content scanning, policy
"""
from flask import Blueprint, jsonify, request
from agent_friday.core import login_required
from agent_friday.services import federation as fed
from agent_friday.services import federation_transport as transport
from agent_friday.services import marketplace
from agent_friday.services import economy
from agent_friday.services import moderation

federation_bp = Blueprint("federation", __name__)


# ── Federation: Identity ──────────────────────────────────────────────────────

@federation_bp.route("/api/federation/identity", methods=["GET"])
@login_required
def get_identity():
    identity = fed.get_identity()
    peer_card = fed.get_peer_card()
    return jsonify({"ok": True, "identity": identity, "peer_card": peer_card})


# ── Federation: Discovery ─────────────────────────────────────────────────────

@federation_bp.route("/api/federation/discover", methods=["POST"])
@login_required
def discover_peer():
    data = request.get_json(silent=True) or {}
    url = data.get("url") or data.get("endpoint")
    if not url:
        return jsonify({"error": "url required"}), 400
    peer = fed.discover_peer(url)
    if peer is None:
        return jsonify({"error": "discovery failed — could not reach or verify agent at that URL"}), 502
    return jsonify({"ok": True, "peer": peer})


@federation_bp.route("/api/federation/add-peer", methods=["POST"])
@login_required
def add_peer():
    data = request.get_json(silent=True) or {}
    card = data.get("peer_card") or data
    peer = fed.add_peer_card(card)
    if peer is None:
        return jsonify({"error": "invalid peer card — signature check failed or missing required fields"}), 400
    return jsonify({"ok": True, "peer": peer})


# ── Federation: Peers ────────────────────────────────────────────────────────

@federation_bp.route("/api/federation/peers", methods=["GET"])
@login_required
def list_peers():
    peers = fed.get_peers()
    return jsonify({"ok": True, "peers": peers, "count": len(peers)})


@federation_bp.route("/api/federation/peers/<agent_id>", methods=["GET"])
@login_required
def get_peer(agent_id):
    peer = fed.get_peer(agent_id)
    if not peer:
        return jsonify({"error": "peer not found"}), 404
    return jsonify({"ok": True, "peer": peer})


# ── Federation: Encrypted Messaging ─────────────────────────────────────────

@federation_bp.route("/api/federation/inbox", methods=["POST"])
def federation_inbox():
    """Receive an encrypted federation message from a peer."""
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "empty envelope"}), 400

    # Rate-limit by sender
    sender_pubkey = data.get("sender_pubkey", "")
    if sender_pubkey and not transport.check_rate_limit(sender_pubkey):
        return jsonify({"error": "rate limit exceeded"}), 429

    result = transport.decrypt_message(data)
    if not result.get("ok"):
        return jsonify({"error": "decryption failed", "detail": result.get("error")}), 400

    payload = result.get("payload") or {}
    msg_type = result.get("msg_type", "")

    # Dispatch to handler
    response_payload = _handle_federation_message(msg_type, payload, result["sender_pubkey"])
    return jsonify({"ok": True, "msg_type": msg_type, "response": response_payload})


@federation_bp.route("/api/federation/send", methods=["POST"])
@login_required
def send_federation_message():
    data = request.get_json(silent=True) or {}
    peer_endpoint = data.get("endpoint")
    recipient_pubkey = data.get("recipient_pubkey")
    msg_type = data.get("msg_type")
    payload = data.get("payload") or {}

    if not (peer_endpoint and recipient_pubkey and msg_type):
        return jsonify({"error": "endpoint, recipient_pubkey, and msg_type required"}), 400

    envelope = transport.build_message(msg_type, payload, recipient_pubkey)
    if not envelope:
        return jsonify({"error": "failed to build encrypted envelope"}), 500

    result = transport.send_to_peer(peer_endpoint, envelope)
    return jsonify({"ok": result.get("ok", False), "result": result})


# ── Federation: Well-known endpoint (for discovery by peers) ─────────────────

@federation_bp.route("/.well-known/friday-agent.json", methods=["GET"])
def well_known_agent():
    """Standard discovery endpoint — serves the signed peer card."""
    card = fed.get_peer_card()
    return jsonify(card)


# ── Marketplace: Listings ─────────────────────────────────────────────────────

@federation_bp.route("/api/marketplace/listings", methods=["GET"])
@login_required
def browse_listings():
    media_type = request.args.get("media_type")
    creator_pubkey = request.args.get("creator_pubkey")
    min_price = int(request.args.get("min_price", 0))
    max_price_raw = request.args.get("max_price")
    max_price = int(max_price_raw) if max_price_raw else None
    license_type = request.args.get("license_type")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    listings = marketplace.search_listings(
        media_type=media_type,
        creator_pubkey=creator_pubkey,
        min_price=min_price,
        max_price=max_price,
        license_type=license_type,
        limit=limit,
        offset=offset,
    )
    return jsonify({"ok": True, "listings": listings, "count": len(listings)})


@federation_bp.route("/api/marketplace/listings", methods=["POST"])
@login_required
def create_listing():
    data = request.get_json(silent=True) or {}
    asset_id = data.get("asset_id")
    if not asset_id:
        return jsonify({"error": "asset_id required"}), 400

    listing = marketplace.create_listing(
        asset_id=asset_id,
        price_mpsi=int(data.get("price_mpsi", 0)),
        license_offered=data.get("license_offered", "CC-BY-4.0"),
        visibility=data.get("visibility", "public"),
        title=data.get("title"),
        description=data.get("description"),
        preview_url=data.get("preview_url"),
    )
    if not listing:
        return jsonify({"error": "listing creation failed"}), 500
    return jsonify({"ok": True, "listing": listing})


@federation_bp.route("/api/marketplace/listings/mine", methods=["GET"])
@login_required
def my_listings():
    creator_pubkey = request.args.get("creator_pubkey")
    listings = marketplace.get_my_listings(creator_pubkey=creator_pubkey)
    return jsonify({"ok": True, "listings": listings, "count": len(listings)})


@federation_bp.route("/api/marketplace/listing/<listing_id>", methods=["GET"])
@login_required
def get_listing(listing_id):
    listing = marketplace.get_listing(listing_id)
    if not listing:
        return jsonify({"error": "listing not found"}), 404
    return jsonify({"ok": True, "listing": listing})


@federation_bp.route("/api/marketplace/listing/<listing_id>", methods=["PATCH"])
@login_required
def update_listing(listing_id):
    data = request.get_json(silent=True) or {}
    updated = marketplace.update_listing(listing_id, **data)
    if not updated:
        return jsonify({"error": "listing not found or update failed"}), 404
    return jsonify({"ok": True, "listing": updated})


@federation_bp.route("/api/marketplace/listing/<listing_id>", methods=["DELETE"])
@login_required
def remove_listing(listing_id):
    ok = marketplace.remove_listing(listing_id)
    if not ok:
        return jsonify({"error": "listing not found"}), 404
    return jsonify({"ok": True})


# ── Marketplace: Purchase ─────────────────────────────────────────────────────

@federation_bp.route("/api/marketplace/purchase", methods=["POST"])
@login_required
def purchase_content():
    data = request.get_json(silent=True) or {}
    listing_id = data.get("listing_id")
    buyer_agent_id = data.get("buyer_agent_id")
    if not listing_id:
        return jsonify({"error": "listing_id required"}), 400

    # Two-step: intent first
    if data.get("confirm"):
        result = marketplace.complete_purchase(
            invoice_id=data.get("invoice_id", ""),
            buyer_agent_id=buyer_agent_id or "",
            payment_confirmed=True,
        )
        if not result or not result.get("ok"):
            return jsonify({"error": "purchase failed", "detail": result}), 400
        return jsonify({"ok": True, "receipt": result.get("receipt"), "transfer": result.get("transfer_record")})

    intent = marketplace.purchase_intent(
        listing_id=listing_id,
        buyer_agent_id=buyer_agent_id or "",
    )
    if not intent or not intent.get("ok"):
        return jsonify({"error": "purchase blocked by policy or listing unavailable",
                        "detail": intent}), 400
    return jsonify({"ok": True, "invoice": intent.get("invoice"), "listing": intent.get("listing")})


# ── Marketplace: Policy ───────────────────────────────────────────────────────

@federation_bp.route("/api/marketplace/policy", methods=["GET"])
@login_required
def get_marketplace_policy():
    policy = marketplace.get_policy()
    return jsonify({"ok": True, "policy": policy})


@federation_bp.route("/api/marketplace/policy", methods=["PUT"])
@login_required
def update_marketplace_policy():
    data = request.get_json(silent=True) or {}
    policy = marketplace.update_policy(data)
    return jsonify({"ok": True, "policy": policy})


# ── Economy: Wallet ───────────────────────────────────────────────────────────

@federation_bp.route("/api/economy/wallet", methods=["GET"])
@login_required
def get_wallet():
    agent_id = request.args.get("agent_id")
    if not agent_id:
        # Default to own wallet
        identity = fed.get_identity()
        agent_id = identity.get("agent_id", "")
    if not agent_id:
        return jsonify({"error": "no agent_id available (Ed25519 key not loaded)"}), 503
    wallet = economy.get_wallet(agent_id)
    if wallet is None:
        return jsonify({"error": "wallet not found"}), 404
    return jsonify({"ok": True, "wallet": wallet})


@federation_bp.route("/api/economy/wallet/genesis", methods=["POST"])
@login_required
def claim_genesis():
    identity = fed.get_identity()
    agent_id = identity.get("agent_id", "")
    if not agent_id:
        return jsonify({"error": "no agent identity"}), 503
    result = economy.apply_genesis_bonus(agent_id)
    if result is None:
        return jsonify({"ok": False, "message": "already claimed or not eligible"})
    return jsonify({"ok": True, "bonus": result})


# ── Economy: Transfer ─────────────────────────────────────────────────────────

@federation_bp.route("/api/economy/transfer", methods=["POST"])
@login_required
def transfer_positrons():
    data = request.get_json(silent=True) or {}
    to_agent = data.get("to_agent")
    amount_mpsi = data.get("amount_mpsi")
    reason = data.get("reason", "manual transfer")

    if not to_agent:
        return jsonify({"error": "to_agent required"}), 400
    if not amount_mpsi or int(amount_mpsi) <= 0:
        return jsonify({"error": "amount_mpsi must be a positive integer"}), 400

    identity = fed.get_identity()
    from_agent = data.get("from_agent") or identity.get("agent_id", "")
    if not from_agent:
        return jsonify({"error": "no agent identity"}), 503

    tx = economy.transfer(from_agent, to_agent, int(amount_mpsi), reason)
    if not tx:
        return jsonify({"error": "transfer failed — insufficient balance or invalid agents"}), 400
    return jsonify({"ok": True, "transaction": tx})


@federation_bp.route("/api/economy/earn", methods=["POST"])
@login_required
def earn_positrons():
    data = request.get_json(silent=True) or {}
    agent_id = data.get("agent_id")
    amount_mpsi = int(data.get("amount_mpsi", 0))
    reason = data.get("reason", "content creation")

    if not agent_id:
        identity = fed.get_identity()
        agent_id = identity.get("agent_id", "")
    if not agent_id:
        return jsonify({"error": "no agent identity"}), 503
    if amount_mpsi <= 0:
        return jsonify({"error": "amount_mpsi must be positive"}), 400

    tx = economy.earn(agent_id, amount_mpsi, reason)
    if not tx:
        return jsonify({"error": "earn failed"}), 500
    wallet = economy.get_wallet(agent_id)
    return jsonify({"ok": True, "transaction": tx, "wallet": wallet})


# ── Economy: Transactions ─────────────────────────────────────────────────────

@federation_bp.route("/api/economy/transactions", methods=["GET"])
@login_required
def get_transactions():
    agent_id = request.args.get("agent_id")
    if not agent_id:
        identity = fed.get_identity()
        agent_id = identity.get("agent_id", "")
    limit = min(int(request.args.get("limit", 50)), 500)
    txs = economy.get_transactions(agent_id, limit=limit)
    return jsonify({"ok": True, "transactions": txs, "count": len(txs)})


# ── Economy: Leaderboard ──────────────────────────────────────────────────────

@federation_bp.route("/api/economy/leaderboard", methods=["GET"])
@login_required
def get_leaderboard():
    limit = min(int(request.args.get("limit", 20)), 100)
    board = economy.get_leaderboard(limit=limit)
    return jsonify({"ok": True, "leaderboard": board, "count": len(board)})


# ── Moderation: Scan ──────────────────────────────────────────────────────────

@federation_bp.route("/api/moderation/scan", methods=["POST"])
@login_required
def scan_content():
    data = request.get_json(silent=True) or {}
    content_text = data.get("content") or data.get("content_text")
    content_path = data.get("content_path")
    content_type = data.get("content_type", "text")
    metadata = data.get("metadata")

    result = moderation.scan(
        content_text=content_text,
        content_path=content_path,
        content_type=content_type,
        metadata=metadata,
    )
    status = 200 if not result.get("blocked") else 422
    return jsonify(result), status


# ── Moderation: Policy ────────────────────────────────────────────────────────

@federation_bp.route("/api/moderation/policy", methods=["GET"])
@login_required
def get_moderation_policy():
    policy = moderation.get_policy()
    return jsonify({"ok": True, "policy": policy})


@federation_bp.route("/api/moderation/policy", methods=["PUT"])
@login_required
def update_moderation_policy():
    data = request.get_json(silent=True) or {}
    policy = moderation.update_policy(data)
    return jsonify({"ok": True, "policy": policy})


# ── Internal: message dispatch ───────────────────────────────────────────────

def _handle_federation_message(msg_type: str, payload: dict, sender_pubkey: str) -> dict:
    """Route an incoming decrypted federation message to the right handler."""
    if msg_type == "HANDSHAKE":
        manifest = payload.get("manifest")
        peer_card = payload.get("peer_card")
        result = fed.handshake(manifest, peer_card)
        if result.get("ok") and peer_card:
            fed.add_peer_card(peer_card)
        return result

    if msg_type == "HEARTBEAT":
        obs = {"type": "heartbeat", "timestamp": payload.get("timestamp", ""), "note": "heartbeat received"}
        fed.update_peer_trust(sender_pubkey, obs)
        identity = fed.get_identity()
        return {"type": "HEARTBEAT_ACK", "agent_id": identity.get("agent_id")}

    if msg_type == "TRUST_ATTESTATION":
        obs = payload.get("observation") or {}
        fed.update_peer_trust(sender_pubkey, obs)
        return {"accepted": True}

    if msg_type == "LICENSE_QUERY":
        asset_id = payload.get("asset_id", "")
        try:
            from agent_friday.services import ownership
            asset = ownership.get_asset(asset_id)
            if asset:
                return {"found": True, "license": asset.get("license"), "asset_id": asset_id}
        except Exception:
            pass
        return {"found": False, "asset_id": asset_id}

    return {"msg_type": msg_type, "status": "received"}
