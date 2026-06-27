"""
Agent Friday — Content Provenance (Layer 2: Ownership & Provenance)
FutureSpeak.AI · Asimov's Mind

Every artifact Friday creates gets a signed, C2PA-aligned "Content Credential"
manifest so the user can prove — cryptographically, forever, without trusting
Friday or FutureSpeak.AI — that they own it and on what terms.

This module is the one place provenance is built, signed, stored, verified, and
traced. It reuses the Ed25519 keypair that already ships in
``proof_of_integrity.IntegrityEngine`` (``sign_payload`` / ``verify_payload``):
there is NO new signing code here, only a new *payload shape*.

Storage mirrors the existing ``creations_meta/`` sidecar pattern:
  • a per-artifact sidecar    ~/.friday/provenance/<content_hash>.jsonld
  • an append-only ledger     ~/.friday/provenance/ledger.jsonl  (hash-chained,
                              like the existing memory_ledger.jsonl, so creation
                              ORDER is locally tamper-evident even offline)

Design rules (consistent with the rest of the tree): PURE-ish — no model calls,
lazy crypto import (import-safe under FRIDAY_TESTING and offline), never raises
out of the public helpers (a provenance failure must never break a generation).
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import agent_friday.core as core
from agent_friday.core import FRIDAY_DIR

PROVENANCE_DIR = FRIDAY_DIR / "provenance"
LEDGER_FILE = PROVENANCE_DIR / "ledger.jsonl"
_USER_ID_FILE = FRIDAY_DIR / "user_id"

CONTEXT_URI = "https://futurespeak.ai/provenance/v1"
MANIFEST_VERSION = "1.0"

# The license terms Friday surfaces at creation (§10). There is no account-wide
# default — the question is always surfaced — but when a caller does not specify
# (e.g. the autonomous daily creation), we fall back to the most conservative,
# owner-holds-everything choice: all-rights-reserved to the user.
LICENSE_TERMS = (
    "all-rights-reserved",      # owner keeps every right (safe default)
    "CC-BY-4.0",
    "CC-BY-SA-4.0",
    "CC0",                      # public domain / free commons
    "priced",                   # commerce layer (Layer 3)
    "custom",
)
DEFAULT_LICENSE_TERMS = "all-rights-reserved"

_LOCK = threading.RLock()


# ═══════════════════════════════════════════════════════════════════════════
#  IDENTITY — stable local user id + the agent's Ed25519 public key.
# ═══════════════════════════════════════════════════════════════════════════

def user_id() -> str:
    """A stable, local, non-PII user id minted once and reused. This is the
    human owner's handle inside provenance — never an email or a real name."""
    try:
        if _USER_ID_FILE.exists():
            val = _USER_ID_FILE.read_text(encoding="utf-8").strip()
            if val:
                return val
    except Exception:
        pass
    val = f"user-{uuid.uuid4().hex[:16]}"
    try:
        FRIDAY_DIR.mkdir(parents=True, exist_ok=True)
        _USER_ID_FILE.write_text(val, encoding="utf-8")
    except Exception:
        pass
    return val


def _integrity():
    """The shared IntegrityEngine (Ed25519). Lazy — import-safe without PyNaCl."""
    try:
        from agent_friday.governance.proof_of_integrity import get_integrity_engine
        return get_integrity_engine()
    except Exception:
        return None


def agent_id() -> Optional[str]:
    """The agent identity == the Ed25519 public key hex (or None if unavailable)."""
    eng = _integrity()
    try:
        return eng.get_public_key_hex() if eng else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  HASHING + DETERMINISTIC SERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════

def hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_file(path) -> Optional[str]:
    """SHA-256 of a file's *payload* bytes (the tamper anchor)."""
    try:
        p = Path(path)
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()
    except Exception:
        return None


def hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _deterministic(body: Dict[str, Any]) -> bytes:
    """The exact bytes signed/verified — sorted keys, no signature field."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def _mime_for(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "webp": "image/webp", "svg": "image/svg+xml", "gif": "image/gif",
        "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
        "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg",
        "m4a": "audio/mp4", "flac": "audio/flac",
        "md": "text/markdown", "html": "text/html", "txt": "text/plain",
        "json": "application/json", "pdf": "application/pdf",
    }.get(ext, "application/octet-stream")


def _media_type_for(path: Path) -> str:
    mime = _mime_for(path)
    head = mime.split("/")[0]
    return {"image": "image", "video": "video", "audio": "music",
            "text": "text"}.get(head, "other")


# ═══════════════════════════════════════════════════════════════════════════
#  LICENSE
# ═══════════════════════════════════════════════════════════════════════════

def normalize_license(license: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Coerce a (possibly partial) license dict to the canonical shape. When the
    caller passes nothing, default to all-rights-reserved to the user (§10)."""
    lic = dict(license or {})
    terms = str(lic.get("terms") or DEFAULT_LICENSE_TERMS)
    if terms not in LICENSE_TERMS:
        terms = "custom"
    free = terms in ("CC-BY-4.0", "CC-BY-SA-4.0", "CC0")
    market = lic.get("market") or {}
    out = {
        "terms": terms,
        "attribution": lic.get("attribution") or "",
        "commercial": bool(lic.get("commercial", terms != "all-rights-reserved")),
        "derivatives": lic.get("derivatives", "share-alike" if terms == "CC-BY-SA-4.0" else "allowed"),
        "market": {
            "mode": "priced" if terms == "priced" else "free" if free else "reserved",
            "price": int(market.get("price", 0) or 0),
            "currency": market.get("currency", "PSI"),
            "rail": market.get("rail"),
        },
    }
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  MANIFEST BUILD + SIGN
# ═══════════════════════════════════════════════════════════════════════════

def build_manifest(artifact_path, *, tool_chain: Optional[List[Dict[str, Any]]] = None,
                   sources: Optional[List[Dict[str, Any]]] = None,
                   license: Optional[Dict[str, Any]] = None,
                   media_type: Optional[str] = None,
                   created: Optional[str] = None,
                   friday_version: Optional[str] = None) -> Dict[str, Any]:
    """Build a C2PA-aligned ContentCredential for an artifact on disk.

    Captures WHO (user_id + agent_id), WHEN, WHAT (filename/mime/content_hash/
    bytes), HOW (tool_chain), the SOURCES edge list (provenance DAG), and the
    creator-set LICENSE. Does NOT sign — call sign_manifest() (or write()).
    Never raises; on any error returns a best-effort manifest.
    """
    p = Path(artifact_path)
    content_hash = hash_file(p) or hash_text(str(p))
    try:
        size = p.stat().st_size
    except Exception:
        size = 0
    if not created:
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        from agent_friday.governance.proof_of_integrity import AGENT_VERSION
    except Exception:
        AGENT_VERSION = "5.0.0"
    return {
        "@context": CONTEXT_URI,
        "type": "ContentCredential",
        "version": MANIFEST_VERSION,
        "creator": {"user_id": user_id(), "agent_id": agent_id()},
        "created": created,
        "timestamp_proof": {"type": "local-ledger"},   # RFC-3161 attached opportunistically
        "artifact": {
            "filename": p.name,
            "mime": _mime_for(p),
            "content_hash": content_hash,
            "bytes": size,
        },
        "media_type": media_type or _media_type_for(p),
        "tool_chain": list(tool_chain or []),
        "friday_version": friday_version or AGENT_VERSION,
        "edits": [],
        "sources": list(sources or []),
        "license": normalize_license(license),
    }


def sign_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Attach an Ed25519 signature over the deterministic JSON of the manifest
    body (everything except the signature). Delegates to the existing
    IntegrityEngine.sign_payload — no new signing code. If no key is available
    the manifest is returned with an explicit unsigned marker."""
    body = {k: v for k, v in manifest.items() if k != "signature"}
    payload = _deterministic(body)
    eng = _integrity()
    pubkey = agent_id()
    sig = None
    if eng is not None:
        try:
            sig = eng.sign_payload(payload)
        except Exception:
            sig = None
    manifest["signature"] = {
        "alg": "ed25519",
        "pubkey": pubkey,
        "value": sig or "ed25519_unavailable",
    }
    return manifest


# ═══════════════════════════════════════════════════════════════════════════
#  STORAGE — sidecar + hash-chained ledger
# ═══════════════════════════════════════════════════════════════════════════

def _sidecar_path(content_hash: str) -> Path:
    safe = content_hash.replace("sha256:", "").replace(":", "_")
    return PROVENANCE_DIR / f"{safe}.jsonld"


def _ledger_tail_hash() -> str:
    """Hash of the last ledger line (the chain link), or the genesis seed."""
    try:
        if LEDGER_FILE.exists():
            last = ""
            with LEDGER_FILE.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last = line.strip()
            if last:
                return hashlib.sha256(last.encode("utf-8")).hexdigest()
    except Exception:
        pass
    return "0" * 64


def _append_ledger(content_hash: str, manifest: Dict[str, Any]) -> None:
    """Append a hash-chained entry so creation order is locally tamper-evident."""
    try:
        PROVENANCE_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            prev = _ledger_tail_hash()
            entry = {
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "content_hash": content_hash,
                "media_type": manifest.get("media_type"),
                "creator": manifest.get("creator"),
                "license": manifest.get("license", {}).get("terms"),
                "prev": prev,
            }
            with LEDGER_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        print(f"  [provenance] ledger append failed: {e}")


def store_manifest(manifest: Dict[str, Any]) -> Optional[Path]:
    """Persist a signed manifest as a sidecar and chain it into the ledger."""
    ch = (manifest.get("artifact") or {}).get("content_hash")
    if not ch:
        return None
    try:
        PROVENANCE_DIR.mkdir(parents=True, exist_ok=True)
        path = _sidecar_path(ch)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
                       encoding="utf-8")
        _append_ledger(ch, manifest)
        return path
    except Exception as e:
        print(f"  [provenance] store failed: {e}")
        return None


def write(artifact_path, *, tool_chain=None, sources=None, license=None,
          media_type=None) -> Dict[str, Any]:
    """The one-line hook every generator calls: build → sign → store → return.

    Best-effort and exception-safe so a provenance failure never breaks a
    generation. Returns the signed manifest (or {} on hard failure)."""
    try:
        manifest = build_manifest(artifact_path, tool_chain=tool_chain,
                                  sources=sources, license=license,
                                  media_type=media_type)
        manifest = sign_manifest(manifest)
        store_manifest(manifest)
        return manifest
    except Exception as e:
        print(f"  [provenance] write failed for {artifact_path}: {e}")
        return {}


def get_manifest(content_hash: str) -> Optional[Dict[str, Any]]:
    try:
        p = _sidecar_path(content_hash)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def manifest_for_file(artifact_path) -> Optional[Dict[str, Any]]:
    """Look up the manifest for a file by hashing it and reading its sidecar."""
    ch = hash_file(artifact_path)
    return get_manifest(ch) if ch else None


# ═══════════════════════════════════════════════════════════════════════════
#  VERIFY + TRACE
# ═══════════════════════════════════════════════════════════════════════════

def verify_manifest(manifest_or_path) -> Dict[str, Any]:
    """Verify a manifest: signature (Ed25519), payload hash (recompute against the
    file if present), ledger chain presence, and how many source edges resolve
    locally. Returns {valid, checks:{...}, creator, license, created}."""
    manifest = manifest_or_path
    if isinstance(manifest_or_path, (str, Path)):
        p = Path(manifest_or_path)
        if p.suffix == ".jsonld":
            manifest = json.loads(p.read_text(encoding="utf-8"))
        else:
            manifest = manifest_for_file(p) or {}
    manifest = manifest or {}

    checks: Dict[str, Any] = {}
    sig = manifest.get("signature") or {}
    body = {k: v for k, v in manifest.items() if k != "signature"}
    payload = _deterministic(body)

    # Signature
    sig_val = sig.get("value")
    pubkey = sig.get("pubkey")
    if sig_val and sig_val != "ed25519_unavailable" and pubkey:
        try:
            from agent_friday.governance.proof_of_integrity import IntegrityEngine
            checks["signature_ok"] = IntegrityEngine.verify_payload(payload, sig_val, pubkey)
        except Exception:
            checks["signature_ok"] = False
    else:
        checks["signature_ok"] = None

    # Payload hash — recompute against the actual file when we can find it.
    art = manifest.get("artifact") or {}
    declared = art.get("content_hash")
    checks["hash_ok"] = None
    if declared:
        cand = core.CREATIONS_DIR / art.get("filename", "")
        if cand.exists():
            checks["hash_ok"] = (hash_file(cand) == declared)

    # Ledger chain presence
    checks["chain_ok"] = _ledger_has(declared) if declared else None

    # Source edges resolvable locally
    resolved = missing = 0
    for edge in manifest.get("sources") or []:
        if get_manifest(edge.get("content_hash", "")):
            resolved += 1
        else:
            missing += 1
    checks["sources_resolved"] = resolved
    checks["sources_missing"] = missing

    valid = all(v is True or isinstance(v, int) for v in
                (checks["signature_ok"], checks["hash_ok"], checks["chain_ok"])
                if v is not None)
    return {
        "valid": bool(valid and checks["signature_ok"] is not False
                      and checks["hash_ok"] is not False),
        "checks": checks,
        "creator": manifest.get("creator"),
        "license": manifest.get("license"),
        "created": manifest.get("created"),
        "media_type": manifest.get("media_type"),
    }


def _ledger_has(content_hash: str) -> bool:
    try:
        if not LEDGER_FILE.exists():
            return False
        with LEDGER_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                if content_hash in line:
                    return True
    except Exception:
        pass
    return False


def trace(content_hash: str, _seen=None) -> List[Dict[str, Any]]:
    """Walk the sources DAG back to its roots. Returns a flat list of nodes
    (the final work first, roots last). Edges pointing at content whose manifest
    lives on another node simply aren't resolved here (a Layer 3 op)."""
    _seen = _seen if _seen is not None else set()
    out: List[Dict[str, Any]] = []
    if content_hash in _seen:
        return out
    _seen.add(content_hash)
    m = get_manifest(content_hash)
    if not m:
        return out
    out.append({
        "content_hash": content_hash,
        "media_type": m.get("media_type"),
        "creator": m.get("creator"),
        "license": m.get("license"),
        "filename": (m.get("artifact") or {}).get("filename"),
        "sources": [e.get("content_hash") for e in (m.get("sources") or [])],
    })
    for edge in m.get("sources") or []:
        out.extend(trace(edge.get("content_hash", ""), _seen))
    return out


def set_license(content_hash: str, license: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Owner changes a piece's terms later: append an edit, re-sign, re-store."""
    m = get_manifest(content_hash)
    if not m:
        return None
    m["license"] = normalize_license(license)
    m.setdefault("edits", []).append({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "op": "relicense",
        "by_agent": agent_id(),
        "detail": f"terms → {m['license']['terms']}",
    })
    m = sign_manifest(m)
    store_manifest(m)
    return m


def source_edge(content_hash: str, role: str) -> Dict[str, Any]:
    """Build a single sources[] edge for a composite work (clip/keyframe/score)."""
    m = get_manifest(content_hash) or {}
    return {
        "content_hash": content_hash,
        "role": role,
        "manifest": hash_text(json.dumps(m, sort_keys=True, default=str)) if m else None,
    }
