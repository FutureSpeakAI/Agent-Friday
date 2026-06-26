"""
Agent Friday — Ownership Registry (Layer 2)
FutureSpeak.AI · Asimov's Mind

SQLite-backed index of every artifact Friday creates or receives, with:
  • register()              — index an artifact by content hash; auto-builds its
                             signed provenance manifest if none is passed
  • get_asset()             — retrieve full record by asset-id or content-hash
  • list_by_creator()       — all assets for a given creator pubkey
  • list_all()              — all assets (paginated)
  • transfer()              — append a signed ownership transfer record
  • get_transfers()         — transfer history for an asset
  • provenance_chain()      — walk the sources DAG back to roots
  • verify()                — full integrity: signature + content-hash + chain
                             + registry manifest-hash consistency
  • check_license_compat()  — license compatibility before derivative creation
  • verify_transfer_sig()   — Ed25519 check on a transfer record's signature

The registry is an *index* over the provenance sidecar files — provenance.py
remains the source of truth for manifests; ownership.py adds fast SQL lookup,
transfer history, and license-enforcement logic.

Storage: ~/.friday/ownership.db (SQLite, WAL mode, no ORM)
Design:  import-safe under FRIDAY_TESTING, never raises out of public helpers,
         no model calls, RLock-protected writes.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import core
from core import FRIDAY_DIR
from services import provenance as pv

DB_PATH = FRIDAY_DIR / "ownership.db"
_LOCK = threading.RLock()

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id              TEXT PRIMARY KEY,
    file_path       TEXT,
    content_hash    TEXT UNIQUE NOT NULL,
    manifest_hash   TEXT,
    creator_pubkey  TEXT,
    created_at      TEXT,
    license         TEXT DEFAULT 'all-rights-reserved',
    title           TEXT,
    media_type      TEXT
);

CREATE TABLE IF NOT EXISTS derivatives (
    child_id            TEXT NOT NULL,
    parent_id           TEXT NOT NULL,
    relationship_type   TEXT NOT NULL DEFAULT 'source',
    PRIMARY KEY (child_id, parent_id)
);

CREATE TABLE IF NOT EXISTS transfers (
    id          TEXT PRIMARY KEY,
    from_key    TEXT NOT NULL,
    to_key      TEXT NOT NULL,
    asset_id    TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    signature   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assets_creator  ON assets(creator_pubkey);
CREATE INDEX IF NOT EXISTS idx_assets_hash     ON assets(content_hash);
CREATE INDEX IF NOT EXISTS idx_derivs_child    ON derivatives(child_id);
CREATE INDEX IF NOT EXISTS idx_derivs_parent   ON derivatives(parent_id);
CREATE INDEX IF NOT EXISTS idx_xfers_asset     ON transfers(asset_id);
"""


# ─────────────────────────────────────────────────────────────────────────────
#  CONNECTION HELPERS
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
#  REGISTER
# ─────────────────────────────────────────────────────────────────────────────

def register(
    file_path,
    manifest: Optional[Dict[str, Any]] = None,
    *,
    title: Optional[str] = None,
    auto_build: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Register (or update) an artifact in the ownership registry.

    If *manifest* is None and *auto_build* is True, calls provenance.write()
    to build + sign + store the manifest first, then indexes it here.

    Returns the asset record dict on success, None on failure. Idempotent:
    a second call for the same content_hash updates mutable fields.
    """
    try:
        p = Path(file_path)
        if manifest is None:
            if not auto_build:
                return None
            manifest = pv.write(p)
        if not manifest:
            return None

        art = manifest.get("artifact") or {}
        content_hash = art.get("content_hash") or pv.hash_file(p)
        if not content_hash:
            return None

        manifest_str = json.dumps(manifest, sort_keys=True, default=str)
        manifest_hash = "sha256:" + hashlib.sha256(manifest_str.encode()).hexdigest()
        creator = manifest.get("creator") or {}
        lic = manifest.get("license") or {}
        created_at = manifest.get("created") or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        media_type = manifest.get("media_type") or pv._media_type_for(p)

        # Discover derivative edges from manifest sources
        sources = manifest.get("sources") or []

        with _LOCK:
            with _conn() as con:
                # UPSERT: insert or update all mutable fields on content_hash conflict
                con.execute(
                    """INSERT INTO assets
                         (id, file_path, content_hash, manifest_hash,
                          creator_pubkey, created_at, license, title, media_type)
                       VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(content_hash) DO UPDATE SET
                         manifest_hash = excluded.manifest_hash,
                         file_path     = excluded.file_path,
                         license       = excluded.license,
                         title         = COALESCE(excluded.title, assets.title)
                    """,
                    (
                        str(uuid.uuid4()),
                        str(p),
                        content_hash,
                        manifest_hash,
                        creator.get("agent_id") or pv.agent_id() or "",
                        created_at,
                        lic.get("terms", "all-rights-reserved"),
                        title or p.stem,
                        media_type,
                    ),
                )
                # Fetch the canonical id (may differ after UPSERT)
                row = con.execute(
                    "SELECT id FROM assets WHERE content_hash=?", (content_hash,)
                ).fetchone()
                asset_id = row["id"] if row else None

                # Register derivative edges
                for src in sources:
                    parent_hash = src.get("content_hash", "")
                    role = src.get("role", "source")
                    parent_row = con.execute(
                        "SELECT id FROM assets WHERE content_hash=?", (parent_hash,)
                    ).fetchone()
                    if parent_row and asset_id:
                        con.execute(
                            """INSERT OR IGNORE INTO derivatives
                               (child_id, parent_id, relationship_type)
                               VALUES (?,?,?)""",
                            (asset_id, parent_row["id"], role),
                        )

        return get_asset(content_hash) if asset_id is None else get_asset(asset_id)
    except Exception as e:
        print(f"  [ownership] register failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  QUERY
# ─────────────────────────────────────────────────────────────────────────────

def get_asset(asset_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve a full asset record by asset-id (UUID) or content_hash."""
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM assets WHERE id=? OR content_hash=?",
                (asset_id, asset_id),
            ).fetchone()
            if not row:
                return None
            rec = dict(row)
            # Attach the full manifest from the provenance sidecar when available
            m = pv.get_manifest(rec["content_hash"])
            if m:
                rec["manifest"] = m
            return rec
    except Exception:
        return None


def get_asset_by_hash(content_hash: str) -> Optional[Dict[str, Any]]:
    """Look up an asset record by content hash."""
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM assets WHERE content_hash=?", (content_hash,)
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def list_by_creator(creator_pubkey: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all assets for a given creator pubkey (defaults to this agent's key)."""
    key = creator_pubkey or pv.agent_id() or ""
    try:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM assets WHERE creator_pubkey=? ORDER BY created_at DESC",
                (key,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def list_all(limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """List all registered assets, most recent first (paginated)."""
    try:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM assets ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  TRANSFER
# ─────────────────────────────────────────────────────────────────────────────

def transfer(
    asset_id: str,
    to_key: str,
    signature: str,
    from_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Record a signed ownership transfer (append-only).

    The original creator in the provenance manifest is never changed — this
    records a LICENSE GRANT / rights transfer. The signature field is stored
    as provided; call verify_transfer_sig() to cryptographically verify it.

    Signature convention (for callers who need to produce a verifiable sig):
        payload = sha256(asset_id + to_key + timestamp_iso).digest()
        signature = IntegrityEngine.sign_payload(payload)  → hex string

    Returns the transfer record dict, or None on failure.
    """
    try:
        asset = get_asset(asset_id)
        if not asset:
            return None
        _from = from_key or pv.agent_id() or ""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        transfer_id = str(uuid.uuid4())
        record = {
            "id": transfer_id,
            "from_key": _from,
            "to_key": to_key,
            "asset_id": asset["id"],
            "timestamp": ts,
            "signature": signature,
        }
        with _LOCK:
            with _conn() as con:
                con.execute(
                    """INSERT INTO transfers
                       (id, from_key, to_key, asset_id, timestamp, signature)
                       VALUES (?,?,?,?,?,?)""",
                    (record["id"], record["from_key"], record["to_key"],
                     record["asset_id"], record["timestamp"], record["signature"]),
                )
        return record
    except Exception as e:
        print(f"  [ownership] transfer failed: {e}")
        return None


def get_transfers(asset_id: str) -> List[Dict[str, Any]]:
    """Return all transfer records for an asset (oldest first)."""
    try:
        asset = get_asset(asset_id)
        if not asset:
            return []
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM transfers WHERE asset_id=? ORDER BY timestamp ASC",
                (asset["id"],),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def verify_transfer_sig(transfer_record: Dict[str, Any]) -> bool:
    """
    Verify the Ed25519 signature on a transfer record.
    Expected signed payload: sha256(asset_id + to_key + timestamp).
    Returns False on any error (no key, wrong sig, missing fields).
    """
    try:
        from proof_of_integrity import IntegrityEngine
        payload = hashlib.sha256(
            (transfer_record["asset_id"] +
             transfer_record["to_key"] +
             transfer_record["timestamp"]).encode("utf-8")
        ).digest()
        return IntegrityEngine.verify_payload(
            payload,
            transfer_record["signature"],
            transfer_record["from_key"],
        )
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  PROVENANCE CHAIN
# ─────────────────────────────────────────────────────────────────────────────

def provenance_chain(asset_id: str) -> List[Dict[str, Any]]:
    """
    Return the full provenance chain for an asset (the artifact itself plus all
    ancestors via sources edges, recursively). Uses provenance.trace() over the
    sidecar manifests and enriches each node with the registry record when found.
    Remote source edges (pointing at content on another federation node) are
    not resolved here — those are Layer 3 operations.
    """
    try:
        asset = get_asset(asset_id)
        if not asset:
            # Treat asset_id as a raw content_hash for direct trace
            return pv.trace(asset_id)
        chain = pv.trace(asset["content_hash"])
        enriched = []
        for node in chain:
            reg = get_asset_by_hash(node.get("content_hash", ""))
            enriched.append({**node, **({"registry": reg} if reg else {})})
        return enriched
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  VERIFICATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def verify(asset_id_or_path) -> Dict[str, Any]:
    """
    Full integrity verification for an asset:
      1. Retrieve the manifest from the provenance sidecar
      2. Verify Ed25519 signature via IntegrityEngine.verify_payload
      3. Recompute content hash and compare to manifest (hash_ok)
      4. Check ledger chain presence (chain_ok)
      5. Check registry record exists and manifest_hash matches (registry_ok)
      6. Count locally resolvable source edges

    Returns {valid, checks:{signature_ok, hash_ok, chain_ok, registry_ok,
    sources_resolved, sources_missing}, creator, license, created, asset_id}.
    Never raises.
    """
    try:
        p: Optional[Path] = None
        asset: Optional[Dict[str, Any]] = None

        arg = str(asset_id_or_path)
        # Try as a file path first
        candidate = Path(arg)
        if candidate.exists():
            p = candidate
            asset = get_asset_by_hash(pv.hash_file(p) or "")
        else:
            # Try as asset_id or content_hash
            asset = get_asset(arg)
            if asset and asset.get("file_path"):
                fp = Path(asset["file_path"])
                if fp.exists():
                    p = fp

        # Run provenance.verify_manifest
        if p is not None and p.exists():
            result = pv.verify_manifest(p)
        elif asset:
            manifest = (asset.get("manifest") or
                        pv.get_manifest(asset.get("content_hash", "")))
            result = pv.verify_manifest(manifest or {})
        else:
            return {"valid": False, "checks": {"error": "asset not found"}, "asset_id": None}

        # Extra check: does the registry manifest_hash match the sidecar?
        result.setdefault("checks", {})
        if asset:
            manifest = (asset.get("manifest") or
                        pv.get_manifest(asset.get("content_hash", "")))
            if manifest:
                manifest_str = json.dumps(manifest, sort_keys=True, default=str)
                computed_mh = "sha256:" + hashlib.sha256(manifest_str.encode()).hexdigest()
                result["checks"]["registry_ok"] = (computed_mh == asset.get("manifest_hash"))
            else:
                result["checks"]["registry_ok"] = False
            result["asset_id"] = asset.get("id")
        else:
            result["checks"]["registry_ok"] = False
            result["asset_id"] = None

        return result
    except Exception as e:
        return {"valid": False, "checks": {"error": str(e)}, "asset_id": None}


# ─────────────────────────────────────────────────────────────────────────────
#  LICENSE ENFORCEMENT
# ─────────────────────────────────────────────────────────────────────────────

# Compatibility matrix: (allowed: bool, note: str)
# Outer key = source license, inner key = proposed derivative license.
_LICENSE_COMPAT: Dict[str, Dict[str, tuple]] = {
    "all-rights-reserved": {
        "all-rights-reserved": (False, "source is all-rights-reserved; same-creator override only"),
        "CC-BY-4.0":           (False, "source is all-rights-reserved"),
        "CC-BY-SA-4.0":        (False, "source is all-rights-reserved"),
        "CC0":                 (False, "source is all-rights-reserved"),
        "priced":              (False, "source is all-rights-reserved"),
        "custom":              (False, "source is all-rights-reserved"),
    },
    "CC-BY-4.0": {
        "all-rights-reserved": (True,  "CC-BY allows derivatives; attribution required"),
        "CC-BY-4.0":           (True,  "CC-BY; attribution required"),
        "CC-BY-SA-4.0":        (True,  "CC-BY; attribution + share-alike required"),
        "CC0":                 (False, "CC-BY requires attribution; CC0 waives it"),
        "priced":              (True,  "CC-BY allows commercial derivatives; attribution required"),
        "custom":              (True,  "CC-BY; attribution required; custom terms apply"),
    },
    "CC-BY-SA-4.0": {
        "all-rights-reserved": (False, "CC-BY-SA requires share-alike"),
        "CC-BY-4.0":           (False, "CC-BY-SA requires share-alike"),
        "CC-BY-SA-4.0":        (True,  "CC-BY-SA; attribution + share-alike"),
        "CC0":                 (False, "CC-BY-SA requires attribution and share-alike"),
        "priced":              (False, "CC-BY-SA requires share-alike (free derivative)"),
        "custom":              (False, "CC-BY-SA requires share-alike"),
    },
    "CC0": {
        # CC0 waives all rights — any downstream license is fine
        "all-rights-reserved": (True, "CC0 waives all rights"),
        "CC-BY-4.0":           (True, "CC0 waives all rights"),
        "CC-BY-SA-4.0":        (True, "CC0 waives all rights"),
        "CC0":                 (True, "CC0 waives all rights"),
        "priced":              (True, "CC0 waives all rights"),
        "custom":              (True, "CC0 waives all rights"),
    },
    "priced": {
        # Priced = all-rights-reserved for derivation purposes
        "all-rights-reserved": (False, "priced content requires a license grant"),
        "CC-BY-4.0":           (False, "priced content requires a license grant"),
        "CC-BY-SA-4.0":        (False, "priced content requires a license grant"),
        "CC0":                 (False, "priced content requires a license grant"),
        "priced":              (False, "priced content requires a license grant"),
        "custom":              (False, "priced content requires a license grant"),
    },
    "custom": {
        # Opaque terms — block all derivatives by default
        "all-rights-reserved": (False, "custom license: terms opaque"),
        "CC-BY-4.0":           (False, "custom license: terms opaque"),
        "CC-BY-SA-4.0":        (False, "custom license: terms opaque"),
        "CC0":                 (False, "custom license: terms opaque"),
        "priced":              (False, "custom license: terms opaque"),
        "custom":              (False, "custom license: terms opaque"),
    },
}


def check_license_compat(
    source_asset_id: str,
    derivative_license: str,
    requestor_pubkey: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Check whether creating a derivative with *derivative_license* is permitted
    given the source asset's license terms.

    Same-creator override: if the requestor is the same Ed25519 key as the
    source asset's creator, they may always derive from their own content,
    even if it is all-rights-reserved or priced.

    Returns:
      {allowed, source_license, derivative_license, note, attribution_required}
    """
    try:
        asset = get_asset(source_asset_id)
        if not asset:
            return {
                "allowed": False,
                "source_license": None,
                "derivative_license": derivative_license,
                "note": "source asset not found in registry",
                "attribution_required": False,
            }

        source_license = asset.get("license", "all-rights-reserved")
        compat_map = _LICENSE_COMPAT.get(source_license, {})
        # Fall back to "custom" bucket if the requested license isn't in the map
        dl = derivative_license if derivative_license in compat_map else "custom"
        allowed, note = compat_map.get(dl, (False, "unknown license combination"))

        # Same-creator override
        if not allowed:
            creator_key = asset.get("creator_pubkey", "")
            req_key = requestor_pubkey or pv.agent_id() or ""
            if creator_key and req_key and creator_key == req_key:
                allowed = True
                note = f"same-creator override ({note})"

        attribution_required = source_license in ("CC-BY-4.0", "CC-BY-SA-4.0")
        return {
            "allowed": allowed,
            "source_license": source_license,
            "derivative_license": derivative_license,
            "note": note,
            "attribution_required": attribution_required,
        }
    except Exception as e:
        return {
            "allowed": False,
            "source_license": None,
            "derivative_license": derivative_license,
            "note": f"check failed: {e}",
            "attribution_required": False,
        }
