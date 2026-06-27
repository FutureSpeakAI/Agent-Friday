"""
Source Trust Federation — signed attestation protocol.

The Source Trust Graph (source_trust_graph.py) learns a *local* reputation for
each news source. The Federation lets independent Friday agents share what they
learn without having to trust each other blindly: every observation can be
emitted as an Ed25519-signed **attestation** that any peer can verify against
the signer's public key. A peer imports an attestation only if its signature
checks out, so a malicious peer can't forge another agent's reputation claims.

Attestation format (v1.0)::

    {
      "type": "source_attestation",
      "version": "1.0",
      "agent_id": "<ed25519_public_key_hex>",
      "timestamp": "2026-06-06T12:00:00Z",
      "source_domain": "example.com",
      "observation": {
        "type": "claim_verified|claim_disputed|correction_issued|opinion_unlabeled|...",
        "claim": "short description of the claim",
        "evidence": "what supports or refutes it",
        "counter_sources": ["reuters.com", "apnews.com"]
      },
      "signature": "<ed25519_signature_hex>"
    }

The signature covers a deterministic JSON serialization of the attestation
body (every field except ``signature``), so re-ordering keys or tampering with
any field invalidates it.

Signing reuses the agent's existing Ed25519 attestation key from
proof_of_integrity.py — the same key that signs the Integrity Manifest — so an
agent has exactly one federation identity.

Storage
-------
~/.friday/federation/attestations.jsonl   attestations this agent signed
~/.friday/federation/imported.jsonl       verified attestations from peers
"""

import json
import threading
from datetime import datetime
from pathlib import Path

ATTESTATION_VERSION = "1.0"
ATTESTATION_TYPE = "source_attestation"

_LOCK = threading.Lock()


def _fed_dir(friday_dir=None):
    d = Path(friday_dir or Path.home() / ".friday") / "federation"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _attestations_file(friday_dir=None):
    return _fed_dir(friday_dir) / "attestations.jsonl"


def _imported_file(friday_dir=None):
    return _fed_dir(friday_dir) / "imported.jsonl"


def _engine(governance_key_fn=None, friday_dir=None):
    """Fetch the shared IntegrityEngine (holds the Ed25519 keypair)."""
    from agent_friday.governance.proof_of_integrity import get_integrity_engine
    return get_integrity_engine(friday_dir=friday_dir,
                                governance_key_fn=governance_key_fn)


def _canonical_body(attestation):
    """Deterministic bytes of the attestation body (excludes ``signature``)."""
    body = {k: v for k, v in attestation.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ── Signing / verification ─────────────────────────────────────────

def sign_attestation(source_domain, observation, governance_key_fn=None,
                     friday_dir=None, timestamp=None):
    """Build and Ed25519-sign a source attestation.

    ``observation`` is a dict: {type, claim, evidence, counter_sources}.
    Returns the signed attestation dict, or None if signing is unavailable.
    """
    eng = _engine(governance_key_fn, friday_dir)
    pubkey = eng.get_public_key_hex()
    if not pubkey:
        return None
    obs = observation or {}
    attestation = {
        "type": ATTESTATION_TYPE,
        "version": ATTESTATION_VERSION,
        "agent_id": pubkey,
        "timestamp": timestamp or (datetime.utcnow().isoformat() + "Z"),
        "source_domain": (source_domain or "").strip().lower(),
        "observation": {
            "type": obs.get("type", "claim_verified"),
            "claim": str(obs.get("claim", ""))[:300],
            "evidence": str(obs.get("evidence", ""))[:500],
            "counter_sources": list(obs.get("counter_sources", []) or []),
        },
    }
    sig = eng.sign_payload(_canonical_body(attestation))
    if not sig:
        return None
    attestation["signature"] = sig
    return attestation


def verify_attestation(attestation, public_key=None, governance_key_fn=None,
                       friday_dir=None):
    """Verify an attestation's Ed25519 signature.

    If ``public_key`` is omitted, the attestation's own ``agent_id`` is used
    (self-describing identity — the signature still proves the holder of that
    key produced it; trust in the key itself is established out of band).
    Returns True only if the structure is well-formed and the signature checks.
    """
    if not isinstance(attestation, dict):
        return False
    if attestation.get("type") != ATTESTATION_TYPE:
        return False
    sig = attestation.get("signature")
    pubkey = public_key or attestation.get("agent_id")
    if not sig or not pubkey:
        return False
    from agent_friday.governance.proof_of_integrity import IntegrityEngine
    return IntegrityEngine.verify_payload(_canonical_body(attestation), sig, pubkey)


# ── Local store ─────────────────────────────────────────────────────

def record_attestation(attestation, friday_dir=None):
    """Append a signed attestation this agent produced to the local log."""
    if not attestation:
        return False
    with _LOCK:
        try:
            with _attestations_file(friday_dir).open("a", encoding="utf-8") as f:
                f.write(json.dumps(attestation) + "\n")
            return True
        except Exception:
            return False


def list_attestations(friday_dir=None, limit=500):
    """Return attestations this agent has signed (newest first)."""
    return _read_jsonl(_attestations_file(friday_dir), limit)


def list_imported(friday_dir=None, limit=500):
    """Return verified attestations imported from peers (newest first)."""
    return _read_jsonl(_imported_file(friday_dir), limit)


def _read_jsonl(path, limit):
    out = []
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    out.reverse()
    return out[:limit]


def import_attestation(attestation, governance_key_fn=None, friday_dir=None):
    """Verify and store a peer's attestation.

    Returns {accepted: bool, reason: str}. An attestation is accepted only if
    its signature verifies against its declared agent_id. On acceptance, the
    observation is folded into the local Source Trust Graph (signed by the
    peer's agent_id, so its provenance is preserved).
    """
    if not verify_attestation(attestation, governance_key_fn=governance_key_fn,
                              friday_dir=friday_dir):
        return {"accepted": False, "reason": "signature verification failed"}

    # Don't re-import the exact same attestation twice.
    sig = attestation.get("signature")
    for existing in list_imported(friday_dir, limit=2000):
        if existing.get("signature") == sig:
            return {"accepted": False, "reason": "already imported"}

    with _LOCK:
        try:
            with _imported_file(friday_dir).open("a", encoding="utf-8") as f:
                f.write(json.dumps(attestation) + "\n")
        except Exception:
            return {"accepted": False, "reason": "store write failed"}

    # Fold the peer observation into the local graph, mapped to a dimension.
    try:
        _apply_to_graph(attestation, friday_dir)
    except Exception:
        pass
    return {"accepted": True, "reason": "ok", "agent_id": attestation.get("agent_id")}


# Map federation observation types → (dimension, signal).
_OBS_TYPE_MAP = {
    "claim_verified": ("factual_accuracy", 0.9),
    "claim_disputed": ("factual_accuracy", 0.2),
    "correction_issued": ("correction_behavior", 0.95),
    "opinion_unlabeled": ("opinion_separation", 0.3),
    "opinion_labeled": ("opinion_separation", 0.9),
    "attribution_present": ("source_attribution", 0.9),
    "attribution_absent": ("source_attribution", 0.3),
    "narrative_break": ("narrative_independence", 0.85),
    "minority_claim": ("factual_accuracy", 0.25),
    "prediction_correct": ("prediction_accuracy", 0.9),
    "prediction_wrong": ("prediction_accuracy", 0.2),
}


def _apply_to_graph(attestation, friday_dir=None):
    from agent_friday.source_trust_graph import get_source_trust_graph
    obs = attestation.get("observation") or {}
    otype = obs.get("type", "")
    dim, signal = _OBS_TYPE_MAP.get(otype, (None, None))
    if dim is None:
        return
    g = get_source_trust_graph(friday_dir=friday_dir)
    g.observe(
        attestation.get("source_domain", ""),
        obs_type=otype,
        dimension=dim,
        signal=signal,
        detail=(obs.get("claim") or "")[:200],
        counter_sources=obs.get("counter_sources"),
        signed_by=attestation.get("agent_id", "peer"),
    )
