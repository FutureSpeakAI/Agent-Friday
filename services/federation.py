"""
Agent Friday — Federation Protocol (Layer 3)
FutureSpeak.AI · Asimov's Mind

Agent discovery, identity, and peer registry.
Builds on Ed25519 identity from proof_of_integrity.py.

Public API
----------
get_identity()      → own agent profile dict
get_peer_card()     → signed peer card JSON (for sharing)
discover_peer(url)  → HTTP GET /.well-known/friday-agent.json + handshake + store
get_peers()         → list all known peers
get_peer(agent_id)  → single peer record
add_peer_card(card) → manually register a peer card
update_peer_trust(agent_id, observation_dict) → record trust observation, recalculate
handshake(manifest_dict, peer_card_dict) → verify cLaws + signature, return result

Storage: ~/.friday/federation.db (SQLite, WAL)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import core
from core import FRIDAY_DIR

DB_PATH = FRIDAY_DIR / "federation.db"
FEDERATION_VERSION = "1.0"
DEFAULT_CAPABILITIES = [
    "provenance.verify",
    "content.listings",
    "license.offer",
    "economy.transfer",
    "moderation.scan",
    "trust.attestation",
]

_LOCK = threading.RLock()

# ─────────────────────────────────────────────────────────────────────────────
#  SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS peers (
    agent_id        TEXT PRIMARY KEY,
    label           TEXT,
    endpoints       TEXT DEFAULT '[]',
    capabilities    TEXT DEFAULT '[]',
    first_seen      TEXT,
    last_handshake  TEXT,
    claws_match     INTEGER DEFAULT 0,
    overall_score   REAL DEFAULT 0.5,
    reliability     REAL DEFAULT 0.5,
    honesty         REAL DEFAULT 0.5,
    claws_adherence REAL DEFAULT 0.5,
    competence      REAL DEFAULT 0.5,
    observations    TEXT DEFAULT '[]',
    fed_pref        TEXT DEFAULT 'ask'
);

CREATE TABLE IF NOT EXISTS peer_cards (
    agent_id    TEXT PRIMARY KEY,
    card_json   TEXT,
    imported_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_peers_score ON peers(overall_score);
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


if not getattr(core, "_TESTING", False):
    _ensure_schema()


# ─────────────────────────────────────────────────────────────────────────────
#  INTEGRITY ENGINE ACCESSOR
# ─────────────────────────────────────────────────────────────────────────────

def _get_engine():
    """Return the IntegrityEngine singleton. Never raises."""
    try:
        from proof_of_integrity import get_integrity_engine
        return get_integrity_engine()
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  OWN IDENTITY
# ─────────────────────────────────────────────────────────────────────────────

def get_identity() -> Dict[str, Any]:
    """
    Return this agent's own identity profile.

    Reads display_name from ~/.friday/settings.json when present.
    The agent_id is the hex Ed25519 public key (or a stable UUID fallback).
    """
    try:
        engine = _get_engine()
        agent_id = engine.get_public_key_hex() if engine else None

        display_name = "Friday"
        settings_file = FRIDAY_DIR / "settings.json"
        if settings_file.exists():
            try:
                s = json.loads(settings_file.read_text(encoding="utf-8"))
                display_name = s.get("display_name") or s.get("agent_name") or "Friday"
            except Exception:
                pass

        if not agent_id:
            # Stable UUID fallback stored locally
            id_file = FRIDAY_DIR / ".agent-id"
            if id_file.exists():
                agent_id = id_file.read_text(encoding="utf-8").strip()
            else:
                agent_id = str(uuid.uuid4())
                FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
                id_file.write_text(agent_id, encoding="utf-8")

        return {
            "agent_id": agent_id,
            "display_name": display_name,
            "capabilities": DEFAULT_CAPABILITIES,
            "content_types": ["image", "video", "audio", "text", "model3d"],
            "federation_version": FEDERATION_VERSION,
            "endpoints": [],
        }
    except Exception as e:
        print(f"  [federation] get_identity failed: {e}")
        return {
            "agent_id": "unknown",
            "display_name": "Friday",
            "capabilities": DEFAULT_CAPABILITIES,
            "content_types": [],
            "federation_version": FEDERATION_VERSION,
            "endpoints": [],
        }


def get_peer_card() -> Dict[str, Any]:
    """
    Build and sign this agent's peer card for sharing with federation peers.

    The card body is signed with the Ed25519 key from IntegrityEngine.
    Signature covers the card body serialised as sorted JSON (excluding the
    signature field itself).
    """
    try:
        identity = get_identity()
        engine = _get_engine()

        from proof_of_integrity import CLAWS_TEXT
        manifest_hash = hashlib.sha256(CLAWS_TEXT.encode("utf-8")).hexdigest()

        issued = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        body: Dict[str, Any] = {
            "type": "FridayPeerCard",
            "version": "1.0",
            "agent_id": identity["agent_id"],
            "label": identity["display_name"],
            "endpoints": identity["endpoints"],
            "capabilities": identity["capabilities"],
            "manifest_hash": manifest_hash,
            "issued": issued,
        }

        sig_value: Optional[str] = None
        if engine:
            payload = json.dumps(body, sort_keys=True).encode("utf-8")
            sig_value = engine.sign_payload(payload)

        card = {
            **body,
            "signature": {
                "alg": "ed25519",
                "value": sig_value or "ed25519_unavailable",
            },
        }
        return card
    except Exception as e:
        print(f"  [federation] get_peer_card failed: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
#  PEER DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────

def discover_peer(url: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """
    Discover a federation peer at *url* by fetching its well-known card.

    Steps:
      1. HTTP GET {url}/.well-known/friday-agent.json
      2. Verify the card's Ed25519 signature (_verify_peer_card)
      3. Run handshake (signature + cLaws check)
      4. Persist the peer to the database
      5. Return the peer record dict

    Uses stdlib urllib only. Returns None on any failure.
    """
    try:
        base = url.rstrip("/")
        well_known_url = f"{base}/.well-known/friday-agent.json"
        req = urllib.request.Request(
            well_known_url,
            headers={"Accept": "application/json", "User-Agent": "FridayAgent/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        card = json.loads(raw.decode("utf-8"))

        _verify_peer_card(card)  # raises on hard failure; soft failures logged
        hs = handshake(manifest_dict=None, peer_card=card)
        return _upsert_peer(card, hs, base_url=base)
    except urllib.error.URLError as e:
        print(f"  [federation] discover_peer network error ({url}): {e}")
        return None
    except Exception as e:
        print(f"  [federation] discover_peer failed ({url}): {e}")
        return None


def add_peer_card(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Manually register a peer from a card dict (e.g. QR scan, paste).

    Verifies the Ed25519 signature and persists the peer. Returns the peer
    record on success, None on failure.
    """
    try:
        if not isinstance(card, dict):
            return None
        _verify_peer_card(card)
        hs = handshake(manifest_dict=None, peer_card=card)
        return _upsert_peer(card, hs, base_url=None)
    except Exception as e:
        print(f"  [federation] add_peer_card failed: {e}")
        return None


def _verify_peer_card(card: Dict[str, Any]) -> None:
    """
    Verify the Ed25519 signature on a peer card.

    Extracts the signature, rebuilds the card body (without the signature
    field), and calls IntegrityEngine.verify_payload.  Logs a warning on
    failure but does not raise — callers can still store unverified cards
    with claws_match=0 / low trust.
    """
    try:
        from proof_of_integrity import IntegrityEngine

        sig_block = card.get("signature") or {}
        sig_hex = sig_block.get("value", "")
        agent_id = card.get("agent_id", "")

        if not sig_hex or sig_hex == "ed25519_unavailable":
            print(f"  [federation] peer card for {agent_id} has no signature — accepting unverified")
            return

        # Rebuild the body that was signed (everything except the signature key)
        body = {k: v for k, v in card.items() if k != "signature"}
        payload = json.dumps(body, sort_keys=True).encode("utf-8")

        ok = IntegrityEngine.verify_payload(payload, sig_hex, agent_id)
        if not ok:
            print(f"  [federation] peer card signature FAILED for {agent_id}")
    except Exception as e:
        print(f"  [federation] _verify_peer_card error: {e}")


def _upsert_peer(
    card: Dict[str, Any],
    handshake_result: Dict[str, Any],
    *,
    base_url: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Persist (insert or update) a peer from a card + handshake result."""
    try:
        agent_id = card.get("agent_id") or ""
        if not agent_id:
            return None

        label = card.get("label") or card.get("display_name") or ""
        endpoints_raw = card.get("endpoints") or []
        if base_url and base_url not in endpoints_raw:
            endpoints_raw = [base_url] + list(endpoints_raw)
        endpoints_json = json.dumps(endpoints_raw)
        capabilities_json = json.dumps(card.get("capabilities") or [])
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        claws_match = 1 if handshake_result.get("claws_match") else 0

        with _LOCK:
            with _conn() as con:
                # Peer record
                con.execute(
                    """INSERT INTO peers
                         (agent_id, label, endpoints, capabilities,
                          first_seen, last_handshake, claws_match)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(agent_id) DO UPDATE SET
                         label          = excluded.label,
                         endpoints      = excluded.endpoints,
                         capabilities   = excluded.capabilities,
                         last_handshake = excluded.last_handshake,
                         claws_match    = excluded.claws_match
                    """,
                    (agent_id, label, endpoints_json, capabilities_json,
                     now, now, claws_match),
                )
                # Peer card (raw JSON)
                con.execute(
                    """INSERT INTO peer_cards (agent_id, card_json, imported_at)
                       VALUES (?,?,?)
                       ON CONFLICT(agent_id) DO UPDATE SET
                         card_json   = excluded.card_json,
                         imported_at = excluded.imported_at
                    """,
                    (agent_id, json.dumps(card), now),
                )
        return get_peer(agent_id)
    except Exception as e:
        print(f"  [federation] _upsert_peer failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  PEER QUERIES
# ─────────────────────────────────────────────────────────────────────────────

def get_peers() -> List[Dict[str, Any]]:
    """Return all known peers ordered by overall_score descending."""
    try:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM peers ORDER BY overall_score DESC"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def get_peer(agent_id: str) -> Optional[Dict[str, Any]]:
    """Return a single peer record by agent_id, or None."""
    try:
        with _conn() as con:
            row = con.execute(
                "SELECT * FROM peers WHERE agent_id=?", (agent_id,)
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  TRUST MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

_TRUST_DIMS = ("reliability", "honesty", "claws_adherence", "competence")
_DIM_WEIGHTS = {
    "reliability":     0.30,
    "honesty":         0.30,
    "claws_adherence": 0.25,
    "competence":      0.15,
}
_DECAY_BASE = 0.95  # weight = 0.95 ** weeks_ago


def update_peer_trust(agent_id: str, observation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Record a trust observation and recalculate the peer's overall_score.

    *observation* should contain:
      - timestamp: ISO-8601 string (defaults to now)
      - reliability, honesty, claws_adherence, competence: floats 0..1 (all optional)
      - note: str (optional free-text)

    Overall score is computed as a decayed weighted mean across all recorded
    observations:  weight_i = 0.95 ** weeks_ago_i, then dot-product with dim weights.

    Returns the updated peer record, or None on failure.
    """
    try:
        peer = get_peer(agent_id)
        if peer is None:
            return None

        now = datetime.now(timezone.utc)
        obs_ts = observation.get("timestamp") or now.strftime("%Y-%m-%dT%H:%M:%SZ")
        obs = {**observation, "timestamp": obs_ts}

        existing_obs: List[Dict[str, Any]] = []
        try:
            existing_obs = json.loads(peer.get("observations") or "[]")
        except Exception:
            pass

        existing_obs.append(obs)

        # Recalculate dimension scores as decayed weighted means
        dim_scores: Dict[str, float] = {}
        for dim in _TRUST_DIMS:
            total_w = 0.0
            total_wv = 0.0
            for o in existing_obs:
                if dim not in o:
                    continue
                val = float(o[dim])
                try:
                    dt = datetime.fromisoformat(o["timestamp"].replace("Z", "+00:00"))
                    # Normalise to UTC
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    weeks_ago = max(0.0, (now.replace(tzinfo=timezone.utc) - dt).total_seconds() / 604800)
                except Exception:
                    weeks_ago = 0.0
                w = _DECAY_BASE ** weeks_ago
                total_w += w
                total_wv += w * val
            dim_scores[dim] = (total_wv / total_w) if total_w > 0 else 0.5

        # Weighted aggregate
        overall = sum(dim_scores[d] * _DIM_WEIGHTS[d] for d in _TRUST_DIMS)

        with _LOCK:
            with _conn() as con:
                con.execute(
                    """UPDATE peers SET
                         observations    = ?,
                         reliability     = ?,
                         honesty         = ?,
                         claws_adherence = ?,
                         competence      = ?,
                         overall_score   = ?
                       WHERE agent_id = ?
                    """,
                    (
                        json.dumps(existing_obs),
                        dim_scores["reliability"],
                        dim_scores["honesty"],
                        dim_scores["claws_adherence"],
                        dim_scores["competence"],
                        round(overall, 6),
                        agent_id,
                    ),
                )
        return get_peer(agent_id)
    except Exception as e:
        print(f"  [federation] update_peer_trust failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  HANDSHAKE
# ─────────────────────────────────────────────────────────────────────────────

def handshake(
    manifest_dict: Optional[Dict[str, Any]],
    peer_card: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Verify a peer during federation handshake.

    Checks performed:
      1. claws_hash  — peer's manifest claws_hash == sha256(local CLAWS_TEXT)
                       (skipped when manifest_dict is None)
      2. signature   — Ed25519 sig on the peer card body verifies against agent_id
      3. agent_id    — card agent_id matches the signing pubkey

    Returns:
      {ok, claws_match, agent_id, checks:{claws_hash, signature, agent_id_match}}

    Never raises.
    """
    result: Dict[str, Any] = {
        "ok": False,
        "claws_match": False,
        "agent_id": peer_card.get("agent_id"),
        "checks": {
            "claws_hash": None,
            "signature": False,
            "agent_id_match": False,
        },
    }
    try:
        from proof_of_integrity import CLAWS_TEXT, IntegrityEngine

        agent_id = peer_card.get("agent_id") or ""
        sig_block = peer_card.get("signature") or {}
        sig_hex = sig_block.get("value", "")
        alg = sig_block.get("alg", "")

        # ── 1. cLaws hash check (only when manifest provided) ─────────────────
        if manifest_dict is not None:
            expected_claws = hashlib.sha256(CLAWS_TEXT.encode("utf-8")).hexdigest()
            peer_claws = manifest_dict.get("claws_hash", "")
            claws_ok = peer_claws == expected_claws
            result["checks"]["claws_hash"] = claws_ok
            result["claws_match"] = claws_ok
        else:
            # No manifest supplied — check the card's manifest_hash field
            expected_claws = hashlib.sha256(CLAWS_TEXT.encode("utf-8")).hexdigest()
            card_mh = peer_card.get("manifest_hash") or ""
            claws_ok = card_mh == expected_claws
            result["checks"]["claws_hash"] = claws_ok
            result["claws_match"] = claws_ok

        # ── 2. Ed25519 signature over the card body ────────────────────────────
        sig_ok = False
        if alg == "ed25519" and sig_hex and sig_hex != "ed25519_unavailable":
            body = {k: v for k, v in peer_card.items() if k != "signature"}
            payload = json.dumps(body, sort_keys=True).encode("utf-8")
            sig_ok = IntegrityEngine.verify_payload(payload, sig_hex, agent_id)
        result["checks"]["signature"] = sig_ok

        # ── 3. agent_id matches pubkey (pubkey is the agent_id in our scheme) ──
        # The agent_id IS the hex pubkey, so a valid sig over the card body with
        # agent_id-as-pubkey proves they match.
        result["checks"]["agent_id_match"] = sig_ok and bool(agent_id)

        result["ok"] = all(
            v is True
            for v in result["checks"].values()
            if v is not None
        )
        return result
    except Exception as e:
        result["error"] = str(e)
        return result
