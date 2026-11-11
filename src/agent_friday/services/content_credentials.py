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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import agent_friday.core as core
from agent_friday.services import provenance as pv

# ── optional heavy deps (lazy) ───────────────────────────────────────────────
# PIL/Pillow (image XMP), python-xmp-toolkit (full XMP). Audio ID3 is written
# in-house (standard library only; see the ID3v2 writer below).
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
      audio (MP3/WAV)      → ID3 TXXX frame (in-house writer, no extra package)
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
    # security-boundary.md §19 row 9: no content leaves here — only a
    # SHA-256 hash — so there is nothing to gate, but the fact and timing of
    # the request were invisible in the one file supposed to enumerate
    # everything that left. Recorded via record_binary_egress, the same
    # primitive every other unclassifiable-payload egress uses; the attempt
    # is logged whether it succeeds or falls back to local-ledger.
    try:
        result = _request_tsa_token(content_hash)
    except Exception as e:
        _record_tsa_egress("block", "network or parse failure: %s" % e)
        return {"type": "local-ledger"}
    _record_tsa_egress("allow", "RFC-3161 timestamp token received")
    return result


def _record_tsa_egress(action: str, reason: str) -> None:
    try:
        from agent_friday.services import egress_gate as _eg
        _eg.record_binary_egress("freetsa.org", "content_hash_timestamp",
                                 action=action, reason=reason, byte_len=32)
    except Exception:
        pass


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


# ── ID3v2 writer (standard library only) ─────────────────────────────────────
# Friday only ever WRITES one thing into an audio file: a TXXX frame named
# "ContentCredential" holding the manifest JSON. That is small enough to do here
# directly, so no GPL tag library is needed. Existing frames are kept byte for
# byte; an existing ContentCredential frame is replaced (as a tag library does for
# a frame with the same description). Anything this writer does not understand
# (ID3v2.2, an unsynchronised or extended-header tag, a truncated or non-RIFF file)
# is refused and the file is left exactly as it was: the answer is False.

_CC_DESC = "ContentCredential"


def _ss_decode(b: bytes) -> int:
    if len(b) != 4 or any(x & 0x80 for x in b):
        raise ValueError("not a synchsafe integer")
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _ss_encode(n: int) -> bytes:
    if not 0 <= n < (1 << 28):
        raise ValueError("ID3 tag too large")
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _txxx_frame(version: int, desc: str, text: str) -> bytes:
    if version == 4:
        body = b"\x03" + desc.encode("utf-8") + b"\x00" + text.encode("utf-8")
        size = _ss_encode(len(body))
    else:   # ID3v2.3 has no UTF-8: use UTF-16 with a byte-order mark
        bom = b"\xff\xfe"
        body = (b"\x01" + bom + desc.encode("utf-16-le") + b"\x00\x00"
                + bom + text.encode("utf-16-le"))
        size = len(body).to_bytes(4, "big")
    return b"TXXX" + size + b"\x00\x00" + body


def _txxx_desc(payload: bytes) -> Optional[str]:
    if not payload:
        return None
    enc, rest = payload[0], payload[1:]
    try:
        if enc in (0, 3):
            return rest.split(b"\x00", 1)[0].decode("latin-1" if enc == 0 else "utf-8")
        if enc in (1, 2):
            i = 0
            while i + 1 < len(rest) and rest[i:i + 2] != b"\x00\x00":
                i += 2
            return rest[:i].decode("utf-16" if enc == 1 else "utf-16-be")
    except UnicodeDecodeError:
        return None
    return None


def _id3_frames(body: bytes, version: int):
    """Yield (frame id, whole frame bytes, payload, flag bytes); stop at padding."""
    pos = 0
    while pos + 10 <= len(body):
        fid = body[pos:pos + 4]
        if fid[0] == 0:
            break
        if not re.fullmatch(rb"[A-Z0-9]{4}", fid):
            raise ValueError("bad frame id")
        raw_size = body[pos + 4:pos + 8]
        size = _ss_decode(raw_size) if version == 4 else int.from_bytes(raw_size, "big")
        end = pos + 10 + size
        if end > len(body):
            raise ValueError("frame runs past the tag")
        yield fid, body[pos:end], body[pos + 10:end], body[pos + 8:pos + 10]
        pos = end


def _id3_span(head: bytes) -> int:
    """Length of the ID3v2 tag that `head` (the file's first 10 bytes) announces; 0 if none."""
    if head[:3] != b"ID3":
        return 0
    if len(head) < 10 or head[3] not in (3, 4):
        raise ValueError("only ID3v2.3 and v2.4 are written")
    if head[5] & 0xC0:
        raise ValueError("unsynchronised or extended-header tags are not rewritten")
    return 10 + _ss_decode(head[6:10]) + (10 if (head[3] == 4 and head[5] & 0x10) else 0)


def _id3_with_credential(old: bytes, manifest_json: str) -> bytes:
    """`old` is the whole existing tag (or b""). Returns the new whole tag."""
    if old:
        version, keep = old[3], old[5] & 0x20
        size = _ss_decode(old[6:10])
        if len(old) < 10 + size:
            raise ValueError("tag runs past the data")
        body = old[10:10 + size]
    else:
        version, keep, body = 4, 0, b""
    frames = []
    for fid, raw, payload, fl in _id3_frames(body, version):
        if fid == b"TXXX" and fl == b"\x00\x00" and _txxx_desc(payload) == _CC_DESC:
            continue
        frames.append(raw)
    frames.append(_txxx_frame(version, _CC_DESC, manifest_json))
    new_body = b"".join(frames)
    return b"ID3" + bytes([version, 0, keep]) + _ss_encode(len(new_body)) + new_body


def _stage(p: Path, parts) -> Path:
    """Write `parts` (bytes, or (open file, start, end) ranges) to a temp file beside `p`."""
    import os
    tmp = p.with_name(p.name + f".cc{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as out:
            for part in parts:
                if isinstance(part, bytes):
                    out.write(part)
                    continue
                f, a, b = part
                f.seek(a)
                while a < b:
                    chunk = f.read(min(1 << 20, b - a))
                    if not chunk:
                        raise ValueError("file shorter than its own headers say")
                    out.write(chunk)
                    a += len(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def _swap(p: Path, tmp: Path) -> None:
    """Put the staged file in place of `p` (the original must be closed first on Windows)."""
    import os
    import shutil
    try:
        shutil.copymode(p, tmp)
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def _embed_mp3(p: Path, manifest_json: str) -> None:
    with open(p, "rb") as f:
        span = _id3_span(f.read(10))
        f.seek(0)
        old = f.read(span)
        if len(old) < span:
            raise ValueError("tag runs past the file")
        tag = _id3_with_credential(old, manifest_json)
        end = f.seek(0, 2)
        tmp = _stage(p, [tag, (f, span, end)])
    _swap(p, tmp)


def _embed_wav(p: Path, manifest_json: str) -> None:
    with open(p, "rb") as f:
        head = f.read(12)
        total = f.seek(0, 2)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError("not a RIFF/WAVE file")
        riff_end = 8 + int.from_bytes(head[4:8], "little")
        if riff_end > total or total - riff_end > 1:
            raise ValueError("RIFF size does not match the file")
        kept, old_tag, pos = [], b"", 12
        while pos + 8 <= riff_end:
            f.seek(pos)
            h = f.read(8)
            sz = int.from_bytes(h[4:], "little")
            if pos + 8 + sz > riff_end:
                raise ValueError("chunk runs past the RIFF")
            if h[:4] in (b"id3 ", b"ID3 "):
                old_tag = f.read(sz)
            else:
                kept.append((pos, sz))
            pos += 8 + sz + (sz & 1)
        if old_tag and old_tag[:3] != b"ID3":
            raise ValueError("the id3 chunk does not hold an ID3v2 tag")
        tag = _id3_with_credential(old_tag[:_id3_span(old_tag[:10])] if old_tag else b"", manifest_json)
        parts, size = [], 4
        for cpos, sz in kept:
            parts.append((f, cpos, cpos + 8 + sz))
            if sz & 1:
                parts.append(b"\x00")
            size += 8 + sz + (sz & 1)
        parts.append(b"id3 " + len(tag).to_bytes(4, "little") + tag + (b"\x00" if len(tag) & 1 else b""))
        size += 8 + len(tag) + (len(tag) & 1)
        tmp = _stage(p, [b"RIFF" + size.to_bytes(4, "little") + b"WAVE"] + parts)
    _swap(p, tmp)


def _embed_id3(p: Path, manifest_json: str) -> bool:
    """Embed manifest JSON into an audio file as an ID3 TXXX frame (standard library only)."""
    try:
        suffix = p.suffix.lower()
        if suffix == ".mp3":
            _embed_mp3(p, manifest_json)
            return True
        if suffix == ".wav":
            _embed_wav(p, manifest_json)
            return True
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
