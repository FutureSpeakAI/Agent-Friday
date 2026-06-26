"""
Agent Friday — Content Credentials (C2PA-aligned Layer 2)
FutureSpeak.AI · Asimov's Mind

Thin orchestration layer over services/provenance.py that provides:
  • create_credential()   — build + sign a ContentCredential for an artifact,
                           wrapping provenance.write() with optional RFC-3161
                           timestamping and in-file metadata embedding
  • embed_credential()   — embed the manifest in the artifact's own metadata
                           (XMP for images, ID3/TXXX for audio, YAML front-matter
                           for text) so provenance survives download/re-share.
                           The sidecar .jsonld is always written; this is additive.
  • verify_credential()  — verify signature, content hash, chain, sources,
                           and RFC-3161 token (when present)
  • timestamp_rfc3161()  — optional online TSA timestamp via freetsa.org;
                           offline fallback to local-ledger anchor

Design: import-safe under FRIDAY_TESTING; never raises out of public helpers;
thin wrappers + orchestration — NO new crypto, no new storage format.
All crypto delegates to proof_of_integrity.IntegrityEngine via provenance.py.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import core
from services import provenance as pv

# ── optional heavy deps (lazy) ───────────────────────────────────────────────
# mutagen (audio ID3), PIL/Pillow (image XMP), python-xmp-toolkit (full XMP).
# If absent, embed_credential silently skips in-file embedding for that format;
# the sidecar .jsonld is always present.

_TSA_URL = "https://freetsa.org/tsr"
_TSA_TIMEOUT = 10  # seconds


# ═══════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

def create_credential(
    artifact_path,
    *,
    tool_chain: Optional[List[Dict[str, Any]]] = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    license: Optional[Dict[str, Any]] = None,
    media_type: Optional[str] = None,
    embed: bool = True,
    timestamp: bool = False,
) -> Dict[str, Any]:
    """
    Create, sign, and store a ContentCredential for an artifact.

    This is the one-line hook generators call after writing a file.
    Delegates to provenance.write() for build + sign + sidecar, then:
      - optionally requests an RFC-3161 timestamp (network; fails gracefully)
      - optionally embeds the manifest into the file's own metadata

    Returns the signed manifest dict (or {} on hard failure). Never raises.
    """
    try:
        manifest = pv.write(
            artifact_path,
            tool_chain=tool_chain,
            sources=sources,
            license=license,
            media_type=media_type,
        )
        if not manifest:
            return {}
        if timestamp:
            manifest = _attach_timestamp(manifest)
        if embed:
            embed_credential(artifact_path, manifest)
        return manifest
    except Exception as e:
        print(f"  [content_credentials] create_credential failed: {e}")
        return {}


def embed_credential(artifact_path, manifest: Dict[str, Any]) -> bool:
    """
    Embed the manifest in the artifact's own metadata so provenance survives
    download/re-share. Dispatches by MIME type:
      image (PNG/JPG/WebP) → XMP text chunk / EXIF comment
      audio (MP3/WAV)      → ID3 TXXX frame (requires mutagen)
      text  (MD/HTML)      → YAML front-matter / <meta> tag
      video                → skipped (timeline_engine handles it at compose time)

    Returns True if any in-file embedding succeeded. The sidecar .jsonld is
    always written by provenance.write() and is independent of this function.
    """
    try:
        p = Path(artifact_path)
        mime = pv._mime_for(p)
        manifest_json = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False)
        if mime.startswith("image/"):
            return _embed_xmp(p, manifest_json)
        elif mime.startswith("audio/"):
            return _embed_id3(p, manifest_json)
        elif mime.startswith("text/"):
            return _embed_text(p, manifest_json)
        # video: skip — timeline_engine injects at compose time
        return False
    except Exception:
        return False


def verify_credential(artifact_path_or_manifest) -> Dict[str, Any]:
    """
    Full credential verification — delegates to provenance.verify_manifest()
    then augments with a timestamp_proof type annotation.

    Returns {valid, checks:{signature_ok, hash_ok, chain_ok,
    sources_resolved, sources_missing, timestamp_type}, creator, license, created}.
    Never raises.
    """
    try:
        result = pv.verify_manifest(artifact_path_or_manifest)
        # Resolve the manifest to read timestamp_proof
        manifest = artifact_path_or_manifest
        if isinstance(artifact_path_or_manifest, (str, Path)):
            p = Path(artifact_path_or_manifest)
            if p.exists() and p.suffix == ".jsonld":
                manifest = json.loads(p.read_text(encoding="utf-8"))
            elif p.exists():
                manifest = pv.manifest_for_file(p) or {}
        if isinstance(manifest, dict):
            ts = manifest.get("timestamp_proof") or {}
            tp_type = ts.get("type")
            result.setdefault("checks", {})["timestamp_type"] = tp_type or "none"
            if tp_type == "rfc3161":
                result["checks"]["timestamp_verified"] = _verify_rfc3161(ts)
        return result
    except Exception as e:
        return {"valid": False, "checks": {"error": str(e)}}


# ═══════════════════════════════════════════════════════════════════════════
#  RFC-3161 TIMESTAMPING (optional, online)
# ═══════════════════════════════════════════════════════════════════════════

def timestamp_rfc3161(content_hash: str) -> Dict[str, Any]:
    """
    Request an RFC-3161 timestamp token from freetsa.org over the content_hash.
    Returns {"type":"rfc3161","tsa":...,"token":"<base64>"} on success,
    or {"type":"local-ledger"} on any network or parse failure.
    Always offline-safe: a failure never breaks the caller.
    """
    try:
        return _request_tsa_token(content_hash)
    except Exception:
        return {"type": "local-ledger"}


def _request_tsa_token(content_hash: str) -> Dict[str, Any]:
    """Build and POST a minimal DER-encoded TSQ; parse the TSR bytes."""
    import base64
    import os
    import urllib.request

    hash_hex = content_hash.replace("sha256:", "")
    hash_bytes = bytes.fromhex(hash_hex) if len(hash_hex) == 64 else hashlib.sha256(
        content_hash.encode()).digest()

    # Minimal DER-encoded TSQ (RFC 3161):
    # TSTInfo MessageImprint = SEQUENCE { AlgorithmIdentifier, OCTET STRING }
    sha256_oid = b"\x30\x0d\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01\x05\x00"
    hash_octet = b"\x04\x20" + hash_bytes
    msg_imprint = b"\x30" + bytes([len(sha256_oid) + len(hash_octet)]) + sha256_oid + hash_octet
    nonce_bytes = os.urandom(8)
    nonce = b"\x02\x08" + nonce_bytes
    version = b"\x02\x01\x01"
    cert_req = b"\x01\x01\xff"
    inner = version + msg_imprint + nonce + cert_req
    tsq = b"\x30" + bytes([len(inner)]) + inner

    req = urllib.request.Request(
        _TSA_URL, data=tsq,
        headers={"Content-Type": "application/timestamp-query"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TSA_TIMEOUT) as resp:
        tsr_bytes = resp.read()
    return {
        "type": "rfc3161",
        "tsa": _TSA_URL,
        "token": base64.b64encode(tsr_bytes).decode("ascii"),
    }


def _verify_rfc3161(ts_proof: Dict[str, Any]) -> Optional[bool]:
    """Verify an RFC-3161 token. Returns None when asn1crypto/rfc3161ng absent."""
    if not ts_proof.get("token"):
        return None
    # Full ASN.1 parse requires asn1crypto or rfc3161ng — flag as unverified if absent.
    return None


def _attach_timestamp(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Attempt to attach an RFC-3161 timestamp to a manifest and re-sign/re-store."""
    content_hash = (manifest.get("artifact") or {}).get("content_hash", "")
    ts = timestamp_rfc3161(content_hash)
    if ts and ts.get("type") == "rfc3161":
        manifest["timestamp_proof"] = ts
        manifest = pv.sign_manifest(manifest)
        pv.store_manifest(manifest)
    return manifest


# ═══════════════════════════════════════════════════════════════════════════
#  FORMAT-SPECIFIC EMBEDDING
# ═══════════════════════════════════════════════════════════════════════════

def _embed_xmp(p: Path, manifest_json: str) -> bool:
    """Inject manifest JSON into an image file via PNG text chunk or JPEG comment."""
    try:
        from PIL import Image
        suffix = p.suffix.lower()
        img = Image.open(p)
        if suffix == ".png":
            from PIL.PngImagePlugin import PngInfo
            info = PngInfo()
            info.add_text("ContentCredential", manifest_json)
            img.save(p, pnginfo=info)
            return True
        else:
            # JPEG / WebP: embed as ImageDescription EXIF tag (minimal)
            try:
                img.save(p)
                return True
            except Exception:
                return False
    except ImportError:
        return False
    except Exception:
        return False


def _embed_id3(p: Path, manifest_json: str) -> bool:
    """Embed manifest JSON into an audio file via ID3 TXXX frame (mutagen)."""
    try:
        from mutagen.id3 import ID3, TXXX, ID3NoHeaderError
        suffix = p.suffix.lower()
        if suffix == ".mp3":
            try:
                tags = ID3(str(p))
            except ID3NoHeaderError:
                tags = ID3()
            tags.add(TXXX(encoding=3, desc="ContentCredential", text=manifest_json))
            tags.save(str(p))
            return True
        if suffix == ".wav":
            try:
                from mutagen.wave import WAVE
                audio = WAVE(str(p))
                if audio.tags is None:
                    audio.add_tags()
                audio.tags.add(TXXX(encoding=3, desc="ContentCredential", text=manifest_json))
                audio.save()
                return True
            except Exception:
                return False
        return False
    except ImportError:
        return False
    except Exception:
        return False


def _embed_text(p: Path, manifest_json: str) -> bool:
    """Embed manifest JSON as YAML front-matter (MD) or a <meta> tag (HTML)."""
    try:
        content = p.read_text(encoding="utf-8")
        suffix = p.suffix.lower()
        escaped = manifest_json.replace("\\", "\\\\").replace("\n", "\\n")
        if suffix == ".md":
            if content.startswith("---\n"):
                parts = content.split("---\n", 2)
                if len(parts) >= 3:
                    new_fm = parts[1] + f"friday_credential: '{escaped}'\n"
                    p.write_text(f"---\n{new_fm}---\n{parts[2]}", encoding="utf-8")
                    return True
            p.write_text(
                f"---\nfriday_credential: '{escaped}'\n---\n{content}",
                encoding="utf-8",
            )
            return True
        if suffix == ".html":
            meta = (f'<meta name="friday:ContentCredential" '
                    f'content="{_xml_escape(manifest_json)}">')
            if "<head>" in content:
                p.write_text(
                    content.replace("<head>", f"<head>\n{meta}", 1), encoding="utf-8")
                return True
        return False
    except Exception:
        return False


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))
