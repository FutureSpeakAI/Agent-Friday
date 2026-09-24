"""Signing PDFs: an image stamp, or a real digital signature with pyHanko.

Invariants:

  * Friday never signs without an approval card. `request_signature` only
    raises the card; `sign_approved` is the one function that signs, and it
    re-reads the card itself: kind, status "approved" (never auto-approved),
    not already used, the payload still matching the fingerprint the card was
    created with, and the source file unchanged since. One card, one signature.
  * The card names the file, the page, the method and the placement, and
    carries a rendered preview of that page with the signature box outlined.
  * The signed copy is a new file in Friday's output folder. The source is
    never written.
  * The signature image, the certificate and its passphrase are secrets. They
    are stored only through `credential_store.write_secret` (encrypted at
    rest), set by the owner from Settings, and never returned by any API.
  * The action passes the governance checkpoint's cLaws check and writes a
    signed receipt (`action_gate.record_external`) before anything is signed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from agent_friday.paths import friday_home
from agent_friday.services import pdf_forms

_log = logging.getLogger("friday.pdf_signing")

APPROVAL_KIND = "pdf_signature"
SUBJECT_TYPE = "pdf_document"
MODES = ("stamp", "digital")

_IMAGE = "signature_image.bin"
_CERT = "certificate_p12.bin"
_PASS = "certificate_passphrase.bin"
_MAX_UPLOAD = 5 * 1024 * 1024

# Default placement: 180 x 60 pt, one inch in from the bottom-right corner.
_DEFAULT_W, _DEFAULT_H, _MARGIN = 180.0, 60.0, 72.0


class SignRefused(Exception):
    """Nothing was signed; the message says why."""


# ── Secrets ─────────────────────────────────────────────────────────────────

def signing_dir() -> Path:
    return Path(friday_home()) / "signing"


def _path(name: str) -> Path:
    return signing_dir() / name


def _read(name: str) -> Optional[bytes]:
    from agent_friday.services import credential_store as cs
    p = _path(name)
    if not p.exists():
        return None
    try:
        return cs.read_secret(p)
    except Exception:
        return None


def _write(name: str, data: bytes) -> str:
    from agent_friday.services import credential_store as cs
    return cs.write_secret(_path(name), data)


def _delete(name: str) -> bool:
    p = _path(name)
    if p.exists():
        p.unlink()
        return True
    return False


def _audit(event: str, **fields) -> None:
    try:
        from agent_friday.services import credential_store as cs
        cs.audit_event("pdf_signing", event, **fields)
    except Exception:
        pass


def set_signature_image(data: bytes) -> str:
    """Store the owner's signature image (any format PIL reads), as PNG."""
    if not data or len(data) > _MAX_UPLOAD:
        raise SignRefused("the signature image must be between 1 byte and 5 MB")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            im = im.convert("RGBA")
            im.thumbnail((1200, 600))
            buf = io.BytesIO()
            im.save(buf, "PNG")
    except Exception as e:
        raise SignRefused(f"that is not an image Friday can read: {e}")
    method = _write(_IMAGE, buf.getvalue())
    _audit("signature_image_set", method=method)
    return method


def clear_signature_image() -> bool:
    gone = _delete(_IMAGE)
    _audit("signature_image_cleared", present=gone)
    return gone


def _load_p12(p12: bytes, passphrase: str):
    from cryptography.hazmat.primitives.serialization import pkcs12
    try:
        key, cert, _extra = pkcs12.load_key_and_certificates(
            p12, passphrase.encode("utf-8") if passphrase else None)
    except Exception as e:
        raise SignRefused(f"the certificate file or its passphrase is not valid: {e}")
    if key is None or cert is None:
        raise SignRefused("the certificate file must contain both a certificate "
                          "and its private key (a .p12 / .pfx file)")
    return key, cert


def _cert_summary(cert) -> dict:
    try:
        not_after = cert.not_valid_after_utc
    except AttributeError:                       # older cryptography
        not_after = cert.not_valid_after
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=timezone.utc)
    return {"subject": cert.subject.rfc4514_string(),
            "not_after": not_after.isoformat(), "expired": not_after < now}


def set_certificate(p12: bytes, passphrase: str = "") -> dict:
    """Validate and store a PKCS#12 certificate and its passphrase."""
    if not p12 or len(p12) > _MAX_UPLOAD:
        raise SignRefused("the certificate file must be between 1 byte and 5 MB")
    _key, cert = _load_p12(p12, passphrase or "")
    method = _write(_CERT, p12)
    _write(_PASS, (passphrase or "").encode("utf-8"))
    _audit("certificate_set", method=method)
    return dict(_cert_summary(cert), method=method)


def clear_certificate() -> bool:
    gone = _delete(_CERT)
    _delete(_PASS)
    _audit("certificate_cleared", present=gone)
    return gone


def _secret_state(name: str) -> str:
    if not _path(name).exists():
        return "missing"
    return "stored" if _read(name) is not None else "unreadable"


def status() -> dict:
    """What is set, never the secrets themselves."""
    out = {"signature_image": _secret_state(_IMAGE),
           "certificate": {"state": _secret_state(_CERT)}}
    if out["certificate"]["state"] == "stored":
        try:
            _k, cert = _load_p12(_read(_CERT) or b"", (_read(_PASS) or b"").decode("utf-8"))
            out["certificate"].update(_cert_summary(cert))
        except SignRefused as e:
            out["certificate"]["state"] = "unreadable"
            out["certificate"]["error"] = str(e)
    return out


def _signer():
    from pyhanko.sign import signers
    p12 = _read(_CERT)
    if not p12:
        raise SignRefused("no signing certificate is set. The owner adds one in "
                          "Settings (see docs/user-guide/pdf-documents.md).")
    pw = (_read(_PASS) or b"").decode("utf-8")
    _key, cert = _load_p12(p12, pw)
    if _cert_summary(cert)["expired"]:
        raise SignRefused("the signing certificate has expired")
    signer = signers.SimpleSigner.load_pkcs12_data(
        p12, other_certs=None, passphrase=pw.encode("utf-8") if pw else None)
    if signer is None:
        raise SignRefused("pyHanko could not load the signing certificate")
    return signer


# ── Requests and the card ───────────────────────────────────────────────────

def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _fingerprint(payload: dict) -> str:
    core = {k: payload.get(k) for k in ("source", "source_sha256", "page", "box",
                                         "mode", "reason")}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:24]


def _box(src: Path, page: int, x, y, width, height) -> list:
    pw, ph = pdf_forms.page_size(src, page)
    w = float(width) if width else _DEFAULT_W
    h = float(height) if height else _DEFAULT_H
    x = float(x) if x is not None else max(0.0, pw - _MARGIN - w)
    y = float(y) if y is not None else min(_MARGIN, max(0.0, ph - h))
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > pw + 0.5 or y + h > ph + 0.5:
        raise SignRefused(f"the signature box ({x:.0f}, {y:.0f}, {w:.0f} x {h:.0f} pt) "
                          f"does not fit on the page ({pw:.0f} x {ph:.0f} pt)")
    return [round(x, 2), round(y, 2), round(w, 2), round(h, 2)]


def _describe(payload: dict, pages: int) -> str:
    x, y, w, h = payload["box"]
    how = ("your signature image stamped on the page (a picture of a signature, "
           "not a cryptographic signature)" if payload["mode"] == "stamp" else
           "a digital signature with your certificate (cryptographic; the file "
           "shows it was signed by you)")
    lines = [
        f"Sign {payload['source_name']}, page {payload['page']} of {pages}.",
        f"Method: {how}.",
        f"Placement: a {w:.0f} x {h:.0f} pt box at {x:.0f} pt from the left and "
        f"{y:.0f} pt from the bottom of the page (outlined in red on the preview).",
        f"File: {payload['source']}",
    ]
    if payload.get("reason"):
        lines.append(f"Reason recorded in the signature: {payload['reason']}")
    if payload.get("preview_path"):
        lines.append(f"Preview of the page: {payload['preview_path']}")
    lines.append("A new signed copy is saved; the original file is not changed.")
    return "\n".join(lines)


def request_signature(path, *, page: int = 1, x=None, y=None, width=None,
                      height=None, mode: str = "stamp", reason: str = "",
                      requested_by: str = "friday:sign_pdf") -> dict:
    """Raise (or return the pending) approval card for one signature.
    Signs nothing."""
    from agent_friday.services import approvals as ap
    mode = (mode or "stamp").strip().lower()
    if mode not in MODES:
        raise SignRefused(f"mode must be one of {MODES}")
    try:
        src = pdf_forms.open_pdf(path)
        pages = pdf_forms.page_count(src)
    except pdf_forms.FormRefused as e:
        raise SignRefused(str(e))
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        raise SignRefused("page must be a number")
    if not 1 <= page <= pages:
        raise SignRefused(f"{src.name} has {pages} page(s); page {page} does not exist")
    if mode == "stamp" and _read(_IMAGE) is None:
        raise SignRefused("no signature image is set. The owner adds one in Settings "
                          "(see docs/user-guide/pdf-documents.md).")
    if mode == "digital":
        _signer()                          # fails now rather than after approval
    payload = {"handler": "pdf_sign", "source": str(src), "source_name": src.name,
               "source_sha256": _sha256(src), "page": page,
               "box": _box(src, page, x, y, width, height), "mode": mode,
               "reason": (reason or "")[:200]}
    fp = _fingerprint(payload)
    payload["fingerprint"] = fp
    try:
        png = pdf_forms.render_page_png(src, page, payload["box"])
        prev = pdf_forms.output_dir() / "previews" / f"sign-{fp}.png"
        prev.parent.mkdir(parents=True, exist_ok=True)
        prev.write_bytes(png)
        payload["preview_path"] = str(prev)
        payload["preview_url"] = f"/api/creations/forms/previews/{prev.name}"
    except Exception as e:
        _log.warning("could not render the signing preview: %s", e)
    existing = ap.find_for_subject(SUBJECT_TYPE, fp, APPROVAL_KIND)
    subject = fp
    if existing is not None:
        if existing.get("status") == "pending":
            return existing
        subject = f"{fp}:{uuid.uuid4().hex[:6]}"     # a fresh decision
    text = _describe(payload, pages)
    return ap.create_approval(
        kind=APPROVAL_KIND, subject_type=SUBJECT_TYPE, subject_id=subject,
        title=f"Sign {src.name}, page {page}", description=text,
        action_description=text, force_gate=True, payload=payload,
        requested_by=requested_by)


# ── Signing (only on an approved card) ──────────────────────────────────────

def _digital_sign(src: Path, page: int, box: list, reason: str, field: str) -> bytes:
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    x, y, w, h = box
    writer = IncrementalPdfFileWriter(io.BytesIO(src.read_bytes()))
    meta = signers.PdfSignatureMetadata(field_name=field, reason=reason or None)
    out = io.BytesIO()
    signers.sign_pdf(writer, meta, signer=_signer(), output=out,
                     new_field_spec=fields.SigFieldSpec(
                         field, on_page=page - 1, box=(x, y, x + w, y + h)))
    return out.getvalue()


def sign_approved(approval_id: str, actor: str = "owner") -> dict:
    """Sign the document an approved card names. The ONLY function that signs."""
    from agent_friday.governance import action_gate
    from agent_friday.services import approvals as ap
    rec = ap.get_approval(approval_id)
    if not rec:
        raise SignRefused(f"no such approval: {approval_id}")
    if rec.get("kind") != APPROVAL_KIND:
        raise SignRefused(f"that approval is for {rec.get('kind')!r}, not for signing")
    if rec.get("status") != "approved":
        raise SignRefused(f"this signature is {rec.get('status') or 'undecided'}, not "
                          f"approved by you. Nothing was signed.")
    if rec.get("consumed"):
        raise SignRefused("that approval was already used. One decision, one signature.")
    payload = rec.get("payload") or {}
    fp = _fingerprint(payload)
    if payload.get("handler") != "pdf_sign" or payload.get("fingerprint") != fp \
            or str(rec.get("subject_id") or "").split(":")[0] != fp:
        raise SignRefused("the request changed after you approved it. Nothing was signed.")
    src = Path(payload["source"])
    if not src.is_file() or _sha256(src) != payload.get("source_sha256"):
        raise SignRefused(f"{src.name} changed or moved after you approved signing it. "
                          f"Nothing was signed; ask again.")
    page, box, mode = int(payload["page"]), list(payload["box"]), payload["mode"]
    if mode == "stamp":
        image = _read(_IMAGE)
        if image is None:
            raise SignRefused("the signature image is no longer set. Nothing was signed.")
    try:
        action_gate.record_external("pdf:sign", surface="pdf_signing",
                                    approval_id=approval_id, target=src.name)
    except action_gate.Held as e:
        raise SignRefused(f"held by governance: {e}. Nothing was signed.")
    if mode == "stamp":
        data = pdf_forms.stamp_image(src, image, page, box)
    else:
        data = _digital_sign(src, page, box, payload.get("reason") or "",
                             f"FridaySignature_{fp[:8]}")
    out = pdf_forms.unique_output(f"{src.stem}-signed.pdf")
    if out.resolve() == src.resolve():
        raise SignRefused("the signed copy is never written over the original")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    ap.mark_used(approval_id, actor, {"output": str(out)})
    return {"signed": True, "output": str(out), "mode": mode, "page": page}


# ── Decision hook: approving the card is what signs ─────────────────────────

def _notify(title: str, body: str, kind: str = "info") -> None:
    try:
        import agent_friday.notifications_engine as ne
        ne.push(title=title, body=body, priority="medium", kind=kind, source="pdf_signing")
    except Exception as e:
        _log.warning("could not notify (%s): %s", title, e)


def _on_decision(record: dict) -> None:
    if record.get("kind") != APPROVAL_KIND:
        return
    if (record.get("payload") or {}).get("handler") != "pdf_sign":
        return
    if (record.get("status") or "").lower() != "approved":
        return
    try:
        res = sign_approved(record["approval_id"], actor=record.get("decided_by") or "owner")
        _notify("Signed", f"Saved {Path(res['output']).name}")
    except SignRefused as e:
        _notify("NOT signed", str(e)[:300], kind="warning")
    except Exception as e:
        _log.warning("signing failed: %s", e)
        _notify("NOT signed", f"Signing failed: {str(e)[:250]}", kind="warning")


_HOOKS_REGISTERED = False


def register_hooks() -> None:
    """Idempotent."""
    global _HOOKS_REGISTERED
    if _HOOKS_REGISTERED:
        return
    try:
        from agent_friday.services import approvals as ap
        ap.register_decision_hook(APPROVAL_KIND, _on_decision)
        _HOOKS_REGISTERED = True
    except Exception as e:
        _log.warning("could not register the signing hook: %s", e)


register_hooks()


def decode_b64(value: str) -> bytes:
    """Base64 (optionally a data: URL) to bytes, for the settings routes."""
    s = str(value or "")
    if s.startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    try:
        return base64.b64decode(s, validate=False)
    except Exception:
        raise SignRefused("the upload is not valid base64")
