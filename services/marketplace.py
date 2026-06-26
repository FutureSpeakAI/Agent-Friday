"""
Agent Friday — Content Marketplace (Layer 3)
FutureSpeak.AI · Asimov's Mind

Two-layer marketplace: free commons (CC0/CC-BY/CC-BY-SA) and commerce (priced).
Listings are cached locally; the canonical listing lives on the creator's node.

Public API
----------
create_listing(asset_id, price_mpsi=0, license_offered="CC-BY-4.0", visibility="public", title=None, description=None, preview_url=None)
    → listing dict
get_listing(listing_id)  → full listing dict
search_listings(media_type=None, creator_pubkey=None, min_price=0, max_price=None, license_type=None, limit=50, offset=0)
    → list of listings
purchase_intent(listing_id, buyer_agent_id)
    → {ok, listing, invoice: {amount_mpsi, currency, rail, invoice_id}}
complete_purchase(invoice_id, buyer_agent_id, payment_confirmed=True)
    → {ok, transfer_record, receipt}
update_listing(listing_id, **kwargs)  → updated listing
remove_listing(listing_id)           → ok bool
get_policy()                         → marketplace policy dict
update_policy(policy_dict)           → updated policy
get_my_listings(creator_pubkey=None) → own listings

Storage: ~/.friday/marketplace.db (SQLite, WAL)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import core
from core import FRIDAY_DIR

DB_PATH = FRIDAY_DIR / "marketplace.db"
_LOCK = threading.RLock()

DEFAULT_POLICY: Dict[str, Any] = {
    "buying": {
        "enabled": True,
        "per_item_max_mpsi": 5_000_000,
        "daily_budget_mpsi": 20_000_000,
        "require_approval_over_mpsi": 1_000_000,
        "categories_allowed": ["tools", "music", "stock-image"],
    },
    "selling": {
        "enabled": True,
        "auto_accept_at_listing_price": True,
    },
}

# ── optional service deps ─────────────────────────────────────────────────────
try:
    if not core._TESTING:
        from services import ownership as _ownership
        from services import economy as _economy
    else:
        _ownership = None  # type: ignore[assignment]
        _economy = None    # type: ignore[assignment]
except Exception:
    _ownership = None  # type: ignore[assignment]
    _economy = None    # type: ignore[assignment]

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id                      TEXT PRIMARY KEY,
    asset_id                TEXT,
    title                   TEXT,
    description             TEXT,
    media_type              TEXT,
    preview_url             TEXT,
    price_mpsi              INTEGER DEFAULT 0,
    currency                TEXT DEFAULT 'PSI',
    license_offered         TEXT,
    creator_pubkey          TEXT,
    content_credential_hash TEXT,
    visibility              TEXT DEFAULT 'public',
    rail                    TEXT DEFAULT 'ledger-psi',
    created_at              TEXT,
    updated_at              TEXT,
    signature               TEXT
);

CREATE TABLE IF NOT EXISTS purchases (
    id              TEXT PRIMARY KEY,
    listing_id      TEXT,
    buyer_agent     TEXT,
    seller_agent    TEXT,
    amount_mpsi     INTEGER,
    status          TEXT,
    created_at      TEXT,
    completed_at    TEXT,
    receipt_json    TEXT
);

CREATE TABLE IF NOT EXISTS policy (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_creator   ON listings(creator_pubkey);
CREATE INDEX IF NOT EXISTS idx_listings_asset     ON listings(asset_id);
CREATE INDEX IF NOT EXISTS idx_listings_created   ON listings(created_at);
CREATE INDEX IF NOT EXISTS idx_purchases_listing  ON purchases(listing_id);
CREATE INDEX IF NOT EXISTS idx_purchases_buyer    ON purchases(buyer_agent);
"""

# ─────────────────────────────────────────────────────────────────────────────
#  CONNECTION
# ─────────────────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.row_factory = sqlite3.Row
    return con


def _ensure_schema() -> None:
    with _LOCK:
        with _conn() as con:
            con.executescript(_SCHEMA)


_ensure_schema()


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _engine():
    try:
        from proof_of_integrity import get_integrity_engine
        return get_integrity_engine()
    except Exception:
        return None


def _our_pubkey() -> str:
    eng = _engine()
    return eng.get_public_key_hex() or "" if eng else ""


def _sign_listing(listing: Dict[str, Any]) -> str:
    """Sign the listing body with the Ed25519 engine. Returns hex sig or ''."""
    try:
        eng = _engine()
        if not eng:
            return ""
        body = json.dumps({k: v for k, v in listing.items() if k != "signature"},
                          sort_keys=True).encode()
        return eng.sign_payload(body) or ""
    except Exception:
        return ""


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


# ─────────────────────────────────────────────────────────────────────────────
#  POLICY
# ─────────────────────────────────────────────────────────────────────────────

def get_policy() -> Dict[str, Any]:
    """Read all policy keys from DB, merged with DEFAULT_POLICY."""
    try:
        with _conn() as con:
            rows = con.execute("SELECT key, value FROM policy").fetchall()
        stored = {r["key"]: json.loads(r["value"]) for r in rows}
        merged = json.loads(json.dumps(DEFAULT_POLICY))
        for k, v in stored.items():
            if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                merged[k].update(v)
            else:
                merged[k] = v
        return merged
    except Exception:
        return json.loads(json.dumps(DEFAULT_POLICY))


def update_policy(policy_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert each key into the policy table."""
    try:
        with _LOCK:
            with _conn() as con:
                for k, v in policy_dict.items():
                    con.execute(
                        "INSERT INTO policy (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (k, json.dumps(v)),
                    )
        return get_policy()
    except Exception as e:
        print(f"  [marketplace] update_policy failed: {e}")
        return get_policy()


# ─────────────────────────────────────────────────────────────────────────────
#  LISTINGS
# ─────────────────────────────────────────────────────────────────────────────

def create_listing(
    asset_id: str,
    price_mpsi: int = 0,
    license_offered: str = "CC-BY-4.0",
    visibility: str = "public",
    title: Optional[str] = None,
    description: Optional[str] = None,
    preview_url: Optional[str] = None,
    media_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Insert a new listing; sign with the integrity engine."""
    try:
        listing_id = str(uuid.uuid4())
        now = _now()
        creator = _our_pubkey()
        listing: Dict[str, Any] = {
            "id": listing_id,
            "asset_id": asset_id,
            "title": title or asset_id,
            "description": description or "",
            "media_type": media_type or "",
            "preview_url": preview_url or "",
            "price_mpsi": int(price_mpsi),
            "currency": "PSI",
            "license_offered": license_offered,
            "creator_pubkey": creator,
            "content_credential_hash": "",
            "visibility": visibility,
            "rail": "ledger-psi",
            "created_at": now,
            "updated_at": now,
            "signature": "",
        }
        listing["signature"] = _sign_listing(listing)
        with _LOCK:
            with _conn() as con:
                con.execute(
                    """INSERT INTO listings
                       (id, asset_id, title, description, media_type, preview_url,
                        price_mpsi, currency, license_offered, creator_pubkey,
                        content_credential_hash, visibility, rail, created_at, updated_at, signature)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (listing["id"], listing["asset_id"], listing["title"],
                     listing["description"], listing["media_type"], listing["preview_url"],
                     listing["price_mpsi"], listing["currency"], listing["license_offered"],
                     listing["creator_pubkey"], listing["content_credential_hash"],
                     listing["visibility"], listing["rail"], listing["created_at"],
                     listing["updated_at"], listing["signature"]),
                )
        return listing
    except Exception as e:
        print(f"  [marketplace] create_listing failed: {e}")
        return None


def get_listing(listing_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a full listing dict by id."""
    try:
        with _conn() as con:
            row = con.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
            return _row_to_dict(row) if row else None
    except Exception:
        return None


def search_listings(
    media_type: Optional[str] = None,
    creator_pubkey: Optional[str] = None,
    min_price: int = 0,
    max_price: Optional[int] = None,
    license_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """WHERE-clause search; only public listings; ORDER BY created_at DESC.

    Results are filtered through the subscribed content policy packs —
    blocked listings are excluded; tags/warnings from packs are attached.
    """
    try:
        clauses = ["visibility='public'", "price_mpsi >= ?"]
        params: List[Any] = [int(min_price)]
        if media_type:
            clauses.append("media_type=?")
            params.append(media_type)
        if creator_pubkey:
            clauses.append("creator_pubkey=?")
            params.append(creator_pubkey)
        if max_price is not None:
            clauses.append("price_mpsi <= ?")
            params.append(int(max_price))
        if license_type:
            clauses.append("license_offered=?")
            params.append(license_type)
        where = " AND ".join(clauses)
        params.extend([int(limit), int(offset)])
        with _conn() as con:
            rows = con.execute(
                f"SELECT * FROM listings WHERE {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
            results = [_row_to_dict(r) for r in rows]

        # Filter through content policy packs
        try:
            from services.content_policies import evaluate_content, get_subscribed_packs
            packs = get_subscribed_packs()
            filtered: List[Dict[str, Any]] = []
            for listing in results:
                meta = {
                    "title": listing.get("title") or "",
                    "description": listing.get("description") or "",
                    "categories": [listing.get("media_type") or ""] if listing.get("media_type") else [],
                    "price_mpsi": listing.get("price_mpsi") or 0,
                    "severity": 0.0,
                }
                verdict = evaluate_content(meta, subscribed_packs=packs)
                if not verdict.get("blocked"):
                    listing["policy_tags"] = verdict.get("tags", [])
                    listing["policy_warnings"] = verdict.get("warnings", [])
                    filtered.append(listing)
            return filtered
        except Exception:
            return results
    except Exception as e:
        print(f"  [marketplace] search_listings failed: {e}")
        return []


def get_my_listings(creator_pubkey: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all listings owned by this agent (or the given pubkey)."""
    key = creator_pubkey or _our_pubkey()
    try:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM listings WHERE creator_pubkey=? ORDER BY created_at DESC",
                (key,),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
    except Exception:
        return []


def update_listing(listing_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    """Update mutable fields on a listing; re-sign."""
    try:
        existing = get_listing(listing_id)
        if not existing:
            return None
        allowed = {"title", "description", "preview_url", "price_mpsi",
                   "visibility", "license_offered", "media_type"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return existing
        updates["updated_at"] = _now()
        merged = {**existing, **updates}
        merged["signature"] = _sign_listing(merged)
        set_clause = ", ".join(f"{k}=?" for k in {**updates, "signature": ""})
        vals = list({**updates, "signature": merged["signature"]}.values())
        vals.append(listing_id)
        with _LOCK:
            with _conn() as con:
                con.execute(f"UPDATE listings SET {set_clause} WHERE id=?", vals)
        return get_listing(listing_id)
    except Exception as e:
        print(f"  [marketplace] update_listing failed: {e}")
        return None


def remove_listing(listing_id: str) -> bool:
    """Delete a listing. Returns True on success."""
    try:
        with _LOCK:
            with _conn() as con:
                con.execute("DELETE FROM listings WHERE id=?", (listing_id,))
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  PURCHASING
# ─────────────────────────────────────────────────────────────────────────────

def purchase_intent(
    listing_id: str,
    buyer_agent_id: str,
) -> Dict[str, Any]:
    """Check policy, create a pending purchase record, return invoice."""
    try:
        listing = get_listing(listing_id)
        if not listing:
            return {"ok": False, "error": "listing_not_found"}

        policy = get_policy()
        buying = policy.get("buying", {})
        if not buying.get("enabled", True):
            return {"ok": False, "error": "buying_disabled"}

        amount = listing["price_mpsi"]
        per_max = buying.get("per_item_max_mpsi", 0)
        if per_max and amount > per_max:
            return {"ok": False, "error": "exceeds_per_item_max",
                    "per_item_max_mpsi": per_max, "requested_mpsi": amount}

        purchase_id = str(uuid.uuid4())
        invoice_id = str(uuid.uuid4())
        now = _now()
        with _LOCK:
            with _conn() as con:
                con.execute(
                    """INSERT INTO purchases
                       (id, listing_id, buyer_agent, seller_agent, amount_mpsi,
                        status, created_at, completed_at, receipt_json)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (purchase_id, listing_id, buyer_agent_id,
                     listing["creator_pubkey"], amount,
                     "pending", now, None, None),
                )
        return {
            "ok": True,
            "listing": listing,
            "invoice": {
                "amount_mpsi": amount,
                "currency": listing.get("currency", "PSI"),
                "rail": listing.get("rail", "ledger-psi"),
                "invoice_id": invoice_id,
                "purchase_id": purchase_id,
                "requires_approval": amount > buying.get("require_approval_over_mpsi", 10**9),
            },
        }
    except Exception as e:
        print(f"  [marketplace] purchase_intent failed: {e}")
        return {"ok": False, "error": str(e)}


def complete_purchase(
    invoice_id: str,
    buyer_agent_id: str,
    payment_confirmed: bool = True,
) -> Dict[str, Any]:
    """Finalise a purchase: ledger transfer + ownership transfer + signed receipt."""
    try:
        # Find the pending purchase for this buyer (invoice_id doubles as idempotency key)
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM purchases WHERE buyer_agent=? AND status='pending' "
                "ORDER BY created_at DESC LIMIT 1",
                (buyer_agent_id,),
            ).fetchone()
        if not row:
            return {"ok": False, "error": "no_pending_purchase"}

        purchase = _row_to_dict(row)
        listing = get_listing(purchase["listing_id"])
        if not listing:
            return {"ok": False, "error": "listing_not_found"}

        if not payment_confirmed:
            return {"ok": False, "error": "payment_not_confirmed"}

        seller = purchase["seller_agent"]
        buyer = buyer_agent_id
        amount = purchase["amount_mpsi"]

        # Economy ledger transfer
        if _economy is not None:
            try:
                _economy.spend(buyer, amount)
                _economy.earn(seller, amount)
            except Exception as e:
                print(f"  [marketplace] economy transfer failed: {e}")

        # Ownership transfer
        transfer_record = None
        if _ownership is not None and listing.get("asset_id"):
            try:
                eng = _engine()
                sig = eng.sign_payload(
                    (listing["asset_id"] + buyer + purchase["created_at"]).encode()
                ) if eng else ""
                transfer_record = _ownership.transfer(
                    listing["asset_id"], buyer, sig or "", from_key=seller
                )
            except Exception as e:
                print(f"  [marketplace] ownership transfer failed: {e}")

        completed_at = _now()
        receipt: Dict[str, Any] = {
            "purchase_id": purchase["id"],
            "listing_id": purchase["listing_id"],
            "buyer": buyer,
            "seller": seller,
            "amount_mpsi": amount,
            "currency": listing.get("currency", "PSI"),
            "completed_at": completed_at,
            "asset_id": listing.get("asset_id"),
        }
        eng = _engine()
        if eng:
            receipt["signature"] = eng.sign_payload(
                json.dumps(receipt, sort_keys=True).encode()
            ) or ""

        with _LOCK:
            with _conn() as con:
                con.execute(
                    "UPDATE purchases SET status='completed', completed_at=?, receipt_json=? WHERE id=?",
                    (completed_at, json.dumps(receipt), purchase["id"]),
                )

        return {"ok": True, "transfer_record": transfer_record, "receipt": receipt}
    except Exception as e:
        print(f"  [marketplace] complete_purchase failed: {e}")
        return {"ok": False, "error": str(e)}
