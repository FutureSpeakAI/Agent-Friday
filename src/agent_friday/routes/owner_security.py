"""Settings -> Privacy & Approvals: two owner-only controls.

  * Re-confirm Friday's rules on this PC. The approval checkpoint holds every
    outward action when the signature of the cLaws text under this PC's
    governance key differs from the pinned one. That happens after ~/.friday
    moves to another PC or Windows account (the key lives in Credential
    Manager) and after a release changes the rules text. The owner reads the
    status and re-pins; nothing re-pins on its own.
  * Protect the credential keystore with the vault passphrase. The keystore
    root key sits in ~/.friday/security/keystore.json protected by file
    permissions; wrapping it with the vault passphrase makes the file useless
    on its own. Nothing wraps or unwraps it except this control.

Reads answer whoever the app lets in. Every change also demands a request
from this machine (never a tunnel or a proxy) AND the page's own
X-Friday-Token header, which a cross-site form post cannot carry.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging

from flask import Blueprint, jsonify, request

import agent_friday.core as core
from agent_friday.core import login_required

owner_security_bp = Blueprint("owner_security", __name__)
_log = logging.getLogger("friday.owner_security")


def _refusal() -> str:
    if not core._is_local_request():
        return "Only Friday's own page, open on this PC, can change this."
    if not core._api_token_valid(request.headers.get("X-Friday-Token")):
        return "This request did not come from Friday's page."
    return ""


def _refused(msg: str):
    return jsonify({"ok": False, "message": msg}), 403


# ── Friday's rules (cLaws) ──────────────────────────────────────────────────

def claws_status() -> dict:
    """Where the rules signature stands, without pinning anything."""
    from agent_friday.governance import action_gate as ag
    from agent_friday.governance.proof_of_integrity import CLAWS_TEXT
    out = {"text_sha256": hashlib.sha256(CLAWS_TEXT.encode("utf-8")).hexdigest(),
           "pinned": False, "pinned_at": None}
    pin = ag._gov_dir() / "claws.pin.json"
    try:
        want = None
        if pin.exists():
            doc = json.loads(pin.read_text(encoding="utf-8"))
            want = doc.get("claws_hmac")
            out.update(pinned=bool(want), pinned_at=doc.get("pinned_at"))
        mac = ag.claws_hmac()
    except Exception as e:
        out.update(state="error", holds_outward=True,
                   summary="The rules signature cannot be checked (%s). Outward "
                           "actions are held." % type(e).__name__)
        return out
    if not want:
        out.update(state="not_pinned", holds_outward=False,
                   summary="Not pinned yet. The rules are pinned on this PC the "
                           "first time Friday takes an outward action.")
    elif hmac.compare_digest(str(want), mac):
        out.update(state="intact", holds_outward=False,
                   summary="Intact. The rules match the signature pinned on this PC.")
    else:
        out.update(state="mismatch", holds_outward=True,
                   summary="The rules signature does not match the one pinned "
                           "on this PC, so outward actions are held. This is "
                           "expected after moving Friday's data to another PC or "
                           "Windows account, or after an update that changed "
                           "the rules text.")
    return out


@owner_security_bp.route("/api/governance/claws", methods=["GET"])
@login_required
def governance_claws_status():
    return jsonify({"ok": True, **claws_status()})


@owner_security_bp.route("/api/governance/claws/repin", methods=["POST"])
@login_required
def governance_claws_repin():
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        return jsonify({"ok": False, "message": "Re-confirming needs an explicit "
                                                "confirmation."}), 400
    from agent_friday.governance import action_gate as ag
    before = claws_status()
    try:
        ag._receipt({"tool": "governance:repin_claws", "class": "owner",
                     "decision": "allow", "surface": "settings",
                     "reason": "owner re-confirmed the rules on this PC "
                               "(was: %s)" % before.get("state")})
        ag.repin_claws()
    except Exception as e:
        _log.error("re-pinning the rules failed: %s", e)
        return jsonify({"ok": False, "message": "Could not re-confirm the rules "
                                                "(%s)." % type(e).__name__}), 500
    return jsonify({"ok": True, **claws_status()})


# ── Credential keystore ────────────────────────────────────────────────────

def _vault_passphrase() -> tuple:
    try:
        from agent_friday.services import vault_passphrase as vp
        return vp.resolve()
    except Exception:
        return "", ""


def keystore_status() -> dict:
    from agent_friday.services import keystore as ks
    d = dict(ks.describe())
    d.pop("path", None)
    pw, source = _vault_passphrase()
    d.update(vault_passphrase_set=bool(pw), vault_passphrase_source=source or "")
    return d


@owner_security_bp.route("/api/security/keystore", methods=["GET"])
@login_required
def security_keystore_status():
    return jsonify({"ok": True, **keystore_status()})


@owner_security_bp.route("/api/security/keystore/wrap", methods=["POST"])
@login_required
def security_keystore_wrap():
    """Wrap the root key with the vault passphrase, or remove that wrap.

    The owner types the vault passphrase either way; it must be the one
    Friday resolves at start-up, because that is what unwraps the key on the
    next start. After wrapping, the key is unwrapped once from disk to prove
    the next start will work, and the change is undone if it does not.
    """
    refusal = _refusal()
    if refusal:
        return _refused(refusal)
    body = request.get_json(silent=True) or {}
    wrap = body.get("wrap")
    typed = str(body.get("passphrase") or "")
    if wrap not in ("passphrase", "none"):
        return jsonify({"ok": False, "message": "wrap must be 'passphrase' or 'none'"}), 400
    pw, _source = _vault_passphrase()
    if not pw:
        return jsonify({"ok": False, "message": "Set a vault passphrase first; the "
                                                "keystore is protected with it."}), 400
    if not typed or not hmac.compare_digest(typed.encode("utf-8"), pw.encode("utf-8")):
        return jsonify({"ok": False, "message": "That is not the vault passphrase "
                                                "Friday is using."}), 400
    from agent_friday.services import keystore as ks
    try:
        ks.set_wrap(wrap, pw if wrap == "passphrase" else "")
        ks.root_key(use_cache=False)          # prove the next start can unwrap it
    except Exception as e:
        _log.error("keystore %s wrap failed: %s", wrap, e)
        try:
            ks.set_wrap("none")               # the cached root key is unchanged
        except Exception:
            pass
        return jsonify({"ok": False, "message": "The keystore was left protected by "
                                                "file permissions (%s)."
                                                % type(e).__name__}), 500
    return jsonify({"ok": True, **keystore_status()})
