"""A workspace as an A2UI document, for the owner's phone or tablet, and the one-tap signed edit (contract draft 3).

    GET  /tv/v1/workspaces/<ws>/view                      {ok, ws, view, rev, as_of, ttl_s, doc, data, customization, versions}
    GET  /tv/v1/workspaces/<ws>/view/versions             {ok, rev, versions: [{id, when, kind, by, label}]}
    POST /tv/v1/workspaces/<ws>/view/proposals/challenge  {base_rev, change} -> {ok, proposal_fp, nonce, ttl_s}
    POST /tv/v1/workspaces/<ws>/view/proposals            {base_rev, change, nonce, time, sig} -> {ok, rev, version}

* Reading needs the device read right (pc_read "workspaces"); the data comes ONLY through services/a2ui_broker (D2).
* A proposal (D3) needs the approval right and an active approval key, like approving a card. Its order: the nonce is
  spent BEFORE the signature is looked at; the time is within the window; the APPROVAL key signs
  approval_verify.signing_message("wsview:<ws>", proposal_fp, nonce, device_id, time), where proposal_fp is the
  sha256 of the canonical JSON of {ws, base_rev, change}; then services/a2ui_workspaces applies it through the store
  as a new reversible version (V1-V14 again). pc_act is not widened.
* A stale base is 409 `stale` with the PC's current customization (the phone offers keep mine / keep PC); a change
  that a rule refuses is 400 `refused` naming the rule; every outcome writes a receipt naming the device.
tests/unit/test_tv_ws_view.py holds each rule.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Optional

from flask import Flask, Response, g, jsonify, request

from agent_friday.services import a2ui_broker, a2ui_store, a2ui_workspaces, device_pairing
from agent_friday.tv_gateway import approval_verify as av
from agent_friday.tv_gateway import pc_read, ws_content
from agent_friday.tv_gateway.approval_verify import Refusal, Rejected

_log = logging.getLogger(__name__)

SIGN_PREFIX = "wsview:"


def proposal_fp(ws: str, base_rev: str, change) -> str:
    """sha256 of the canonical JSON of {ws, base_rev, change}. Raises ValueError (UnicodeEncodeError is one) for a body
    that has no canonical bytes: a lone surrogate, NaN or Infinity; _body refuses those first."""
    canon = json.dumps({"ws": ws, "base_rev": base_rev, "change": change}, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _unknown() -> Response:
    return pc_read._refuse(404, "this PC has no such workspace view", code="unknown")


def _served(ws: str) -> Optional[dict]:
    """The device block of a workspace whose A2UI document reads only sections the block declares, or None."""
    return ws_content.view_block(ws)


def _receipt(kind: str, dev: dict, ws: str, **extra) -> None:
    device_pairing._receipt(kind, dev, workspace=ws, **extra)


def _refusal(rejected: Rejected) -> Response:
    return pc_read._refuse(av.HTTP_STATUS[rejected.refusal], rejected.refusal.value.replace("_", " "),
                           code=rejected.refusal.value)


def _approver() -> dict:
    """The current record of an owner device that may approve (right + active key), or Rejected."""
    from agent_friday.tv_gateway.pc_approve_decide import _approving_device
    dev = getattr(g, "device", None)
    if not pc_read.allowed(dev):
        raise Rejected(Refusal.NOT_FOR_DEVICE)
    return _approving_device(dev)


def _body() -> tuple:
    body = request.get_json(silent=True) or {}
    base_rev, change = body.get("base_rev"), body.get("change")
    if not isinstance(base_rev, str) or not base_rev or len(base_rev) > 128 or not isinstance(change, dict):
        raise Rejected(Refusal.BAD_REQUEST, "send {base_rev, change}")
    try:
        proposal_fp("", base_rev, change)
    except ValueError:
        # test_tv_ws_view.py: test_a_challenge_that_cannot_be_fingerprinted_is_refused_not_a_crash,
        # test_a_signed_proposal_that_cannot_be_fingerprinted_is_refused_before_its_nonce
        raise Rejected(Refusal.BAD_REQUEST, "that change has no canonical form (a lone surrogate, NaN or Infinity)")
    return body, base_rev, change


def _verify(dev: dict, ws: str, body: dict, base_rev: str, change: dict, now: float) -> None:
    nonce, sig, when = body.get("nonce"), body.get("sig"), body.get("time")
    if not (isinstance(nonce, str) and isinstance(sig, str) and isinstance(when, int) and not isinstance(when, bool)
            and 0 < when < 2 ** 41):                              # a huge int would overflow the window check (a 500)
        raise Rejected(Refusal.BAD_REQUEST, "send {base_rev, change, nonce, time, sig}")
    challenge = av.consume(dev["id"], SIGN_PREFIX + ws, nonce, now)            # spent before the signature is read
    if abs(now - when) > av.TIME_WINDOW_S:
        raise Rejected(Refusal.BAD_TIME)
    fp = proposal_fp(ws, base_rev, change)
    key = device_pairing.approval_key(dev)
    message = av.signing_message(SIGN_PREFIX + ws, fp, challenge.nonce, dev["id"], when)
    if fp != challenge.action_fp or key is None or not av.signature_holds(key.spki, message, sig):
        device_pairing.note_bad_signature(dev["id"])
        raise Rejected(Refusal.BAD_SIGNATURE)


def register(app: Flask) -> None:
    @app.get("/tv/v1/workspaces/<ws>/view")
    def ws_view(ws):
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        block = _served(ws)
        if block is None:
            return _unknown()
        try:
            view = a2ui_workspaces.current(ws)
            if view is None:
                return _unknown()
            got = a2ui_broker.resolve(view["doc"], sensitivity=block["sensitivity"])
        except a2ui_broker.BrokerRefused as e:
            _log.warning("workspace view %s refused by the broker: %s", ws, e.rule)
            return pc_read._refuse(503, "this view asks for data the PC does not give", code="pc_unavailable")
        except Exception as e:
            _log.warning("workspace view %s failed: %s", ws, type(e).__name__)
            return pc_read._refuse(503, "the PC could not read that right now", code="pc_unavailable")
        return jsonify({"ok": True, "ws": ws, "view": view["view"], "label": view["label"], "rev": view["rev"],
                        "as_of": got["as_of"], "ttl_s": got["ttl_s"], "doc": view["doc"], "data": got["data"],
                        "customization": view["customization"], "versions": view["versions"]})

    @app.get("/tv/v1/workspaces/<ws>/view/versions")
    def ws_view_versions(ws):
        denied = pc_read._guard("workspaces")
        if denied is not None:
            return denied
        if _served(ws) is None:
            return _unknown()
        try:
            view = a2ui_workspaces.current(ws)
            versions = a2ui_store.history(ws) if view and view["stored"] else []
        except Exception as e:
            _log.warning("workspace versions %s failed: %s", ws, type(e).__name__)
            return pc_read._refuse(503, "the PC could not read that right now", code="pc_unavailable")
        return jsonify({"ok": True, "rev": view["rev"] if view else None, "versions": versions})

    @app.post("/tv/v1/workspaces/<ws>/view/proposals/challenge")
    def ws_view_challenge(ws):
        try:
            dev = _approver()
            if _served(ws) is None:
                return _unknown()
            _, base_rev, change = _body()
            challenge = av.mint(dev["id"], SIGN_PREFIX + ws, proposal_fp(ws, base_rev, change))
        except Rejected as rejected:
            return _refusal(rejected)
        return jsonify({"ok": True, "proposal_fp": challenge.action_fp, "nonce": challenge.nonce,
                        "ttl_s": av.NONCE_TTL_S})

    @app.post("/tv/v1/workspaces/<ws>/view/proposals")
    def ws_view_propose(ws):
        now = time.time()
        dev = getattr(g, "device", None) or {}
        try:
            dev = _approver()
            if _served(ws) is None:
                return _unknown()
            body, base_rev, change = _body()
            _verify(dev, ws, body, base_rev, change, now)
        except Rejected as rejected:
            if dev.get("id"):
                _receipt("workspace_view_refused", dev, ws, reason=rejected.refusal.value)
            return _refusal(rejected)
        try:
            got = a2ui_workspaces.apply_change(ws, change, base_rev=base_rev, by=av.device_label(dev["id"]))
        except a2ui_store.ViewStale as stale:
            try:
                current = a2ui_workspaces.current(ws) or {}
            except (a2ui_store.ViewCorrupt, OSError):
                current = {}
            _receipt("workspace_view_refused", dev, ws, reason="stale")
            return pc_read._refuse(409, "your PC's view changed since you read it", code="stale",
                                   current_rev=stale.current_rev,
                                   current={"customization": current.get("customization") or {}})
        except a2ui_store.ViewRefused as refused:
            _receipt("workspace_view_refused", dev, ws, reason="refused", rule=refused.rule)
            return pc_read._refuse(400, refused.detail or "that change is not allowed", code="refused",
                                   rule=refused.rule)
        except PermissionError:
            _receipt("workspace_view_refused", dev, ws, reason="privacy")
            return pc_read._refuse(423, "your PC is off the record; nothing was saved", code="off_record")
        except (a2ui_store.ViewCorrupt, OSError) as e:
            _log.warning("workspace proposal %s could not be stored: %s", ws, type(e).__name__)
            _receipt("workspace_view_refused", dev, ws, reason="pc_unavailable")
            return pc_read._refuse(503, "your PC could not save that right now; nothing changed", code="pc_unavailable")
        _receipt("workspace_view_changed", dev, ws, rev=got["rev"], change=sorted(change))
        return jsonify({"ok": True, "rev": got["rev"], "version": got["version"]})
