"""Settings -> Privacy & Data: two owner-only controls.

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
        want, doc = None, None
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
    if doc is not None and not want:
        # A pin file without a signature: the gate holds outward actions on it
        # (action_gate.verify_claws), so it is a mismatch the owner can resolve here.
        want = "-"
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
        out["changes"] = rules_changes(doc, CLAWS_TEXT)
    return out


#: Lines shown in a change (the rules are a few lines; this bounds a bad pin).
_MAX_CHANGED_LINES = 200
#: A kept text longer than this is not compared at all (the rules are well under 1 KB).
_MAX_KEPT_CHARS = 64_000


def rules_changes(pin_doc: dict, current: str) -> dict:
    """What changed between the rules the owner last confirmed and the rules now
    (north star §13.2, NS-13.2-10), in a form the page shows before she re-confirms.

      kind "text_changed"  the kept text verifies under this PC's key and differs:
                           `lines` lists the changed lines in order ("-" removed,
                           "+" added); `removed` and `added` are the same, split
      kind "key_changed"   the kept copy reads the same as the rules now but cannot be
                           checked under this PC's key (her data moved to another PC
                           or account): the rules now are shown
      kind "unverified"    a kept copy that does not verify and differs: it is not
                           shown (it may not be what she confirmed); the rules now are
      kind "not_kept"      the pin predates keeping the text: the rules now are shown

    Only a kept text signed under this PC's key is ever compared line by line.
    `current` always carries the rules now. `requires_text_sha256` is always true:
    re-confirming sends back the fingerprint of the rules the page displayed, so a
    page showing other rules (an older release, a stale tab) cannot re-confirm these."""
    import difflib
    current = current.replace("\r\n", "\n")
    current_lines = current.split("\n")
    out = {"current": current_lines[:_MAX_CHANGED_LINES], "lines": [], "removed": [], "added": [],
           "truncated": len(current_lines) > _MAX_CHANGED_LINES, "requires_text_sha256": True}
    kept = pin_doc.get("claws_text") if isinstance(pin_doc, dict) else None
    if not isinstance(kept, str):
        out.update(kind="not_kept",
                   words="The rules confirmed earlier were not kept on this PC, so what "
                         "changed cannot be shown. These are the rules now.")
        return out
    kept = kept.replace("\r\n", "\n")
    verifies = False
    if len(kept) <= _MAX_KEPT_CHARS:
        try:
            verifies = hmac.compare_digest(str(pin_doc.get("claws_hmac") or ""), sign_text(kept))
        except Exception:  # noqa: BLE001
            verifies = False
    if not verifies:
        if kept == current:
            out.update(kind="key_changed",
                       words="The rules kept on this PC read the same as the rules now, but "
                             "they cannot be checked under this PC's signing key, as is "
                             "expected after Friday's data moves to another PC or Windows "
                             "account. These are the rules now.")
        else:
            out.update(kind="unverified",
                       words="The rules confirmed earlier cannot be checked on this PC (the "
                             "kept copy does not match its signature), so what changed "
                             "cannot be shown. These are the rules now.")
        return out
    for line in difflib.ndiff(kept.split("\n"), current_lines):
        if line[:2] in ("- ", "+ "):
            out["lines"].append({"op": line[0], "text": line[2:]})
    out["truncated"] = out["truncated"] or len(out["lines"]) > _MAX_CHANGED_LINES
    out["lines"] = out["lines"][:_MAX_CHANGED_LINES]
    out["removed"] = [x["text"] for x in out["lines"] if x["op"] == "-"]
    out["added"] = [x["text"] for x in out["lines"] if x["op"] == "+"]
    out.update(kind="text_changed",
               words="An update changed Friday's rules. Read what changed before "
                     "re-confirming them.")
    return out


def sign_text(text: str) -> str:
    """The pin signature of `text` under this PC's governance key."""
    import hashlib as _hashlib
    from agent_friday.governance import action_gate as ag
    return hmac.new(ag._governance_key(), text.encode("utf-8"), _hashlib.sha256).hexdigest()


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
    changes = before.get("changes") or {}
    if str(body.get("text_sha256") or "") != str(before.get("text_sha256") or ""):
        # Re-confirming names the rules: the page sends back the fingerprint of the
        # rules it displayed, so a page showing other rules cannot confirm these.
        return jsonify({"ok": False, "code": "rules_not_shown",
                        "message": "The rules changed. Read what changed in Settings, then "
                                   "re-confirm the rules shown there.",
                        **before}), 409
    try:
        ag._receipt({"tool": "governance:repin_claws", "class": "owner",
                     "decision": "allow", "surface": "settings",
                     "reason": "owner re-confirmed the rules on this PC "
                               "(was: %s%s)" % (before.get("state"),
                                                ", " + changes["kind"] if changes.get("kind") else "")})
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


@owner_security_bp.route("/api/security/credentials", methods=["GET"])
@login_required
def security_credentials():
    """Every credential Friday holds: what it is, whose, how protected, last changed, last
    used. Names and times only; never a value (credential_store.inventory)."""
    from agent_friday.services import credential_store as cs
    try:
        return jsonify({"ok": True, "credentials": cs.inventory(),
                        "protection": cs.protection_method()})
    except Exception as e:
        _log.error("credential inventory failed: %s", type(e).__name__)
        return jsonify({"ok": False, "message": "The list of credentials could not be read (%s)."
                                                % type(e).__name__}), 500


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
    if wrap == "passphrase":
        from agent_friday.services import keystore_offer as _ko
        if not _ko.passphrase_is_kept(pw):
            # A21 review: a passphrase kept only in this process's environment would lock every
            # saved key at the next start.
            return jsonify({"ok": False, "message": "Your vault passphrase is not saved on this "
                                                    "computer (credential manager or Friday's "
                                                    "protected file), so the next start could not "
                                                    "unlock the keys. Save the passphrase in Settings "
                                                    "first, then lock the keys."}), 400
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
    try:
        # A21: the owner decided in Settings; the one-time card is not needed any more.
        from agent_friday.services import keystore_offer as _ko
        _ko.record("protected" if wrap == "passphrase" else "skipped", "settings")
    except Exception:
        pass
    return jsonify({"ok": True, **keystore_status()})
