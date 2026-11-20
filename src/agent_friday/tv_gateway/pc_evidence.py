"""Triggering the evidence pack from an owner's phone or tablet (K6 brief, decision of 2026-11-17).

Stephen approved full control of the PC from his owner phone. The phone may TRIGGER the pack as a signed owner approval,
like any other approval; the pack is built and SAVED ON THE PC only, the phone sees its status and the PC path, and no
file is sent to the phone.

    POST /tv/v1/evidence/pack          {since, until} (YYYY-MM-DD)  ->  202 {approval_id, state}
    GET  /tv/v1/evidence/pack/<id>     the card's state, then the build's, then {path, filename, entries, manifest_sha256}

The POST raises an approval card for that range; the owner device signs it through the approvals routes
(pc_approve_decide). Nothing is built until the card is approved, and the approval is spent once, so one signature
makes one pack. Reading the status of an approved card is what starts the build (on a worker thread); there is no
other trigger. Every rule is held by a test in tests/unit/test_tv_pc_evidence.py.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Optional

from flask import Flask, g, jsonify, request

from agent_friday.services import approvals, device_pairing
from agent_friday.tv_gateway import pc_read
from agent_friday.tv_gateway.pc_common import allowed, refuse

_log = logging.getLogger(__name__)

KIND = "evidence_pack_export"
SUBJECT = "evidence_pack"
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,96}$")
MAX_DAYS = 400
STATES_OF_A_CARD = {"pending": "waiting_for_approval", "denied": "denied", "expired": "expired", "revoked": "revoked",
                    "blocked": "blocked"}

_JOBS: dict = {}
_LOCK = threading.Lock()


def _guard() -> Optional[object]:
    dev = getattr(g, "device", None)
    if not allowed(dev):
        return refuse(403, "this device cannot trigger the evidence pack", code="pc_evidence_not_for_this_device")
    if not device_pairing.can_approve(dev):
        return refuse(403, "this device has not been allowed to approve for the PC", code="needs_right",
                      needs_right=device_pairing.APPROVAL_RIGHT)
    return None


def _range(body: dict):
    """(since, until, evidence_pack.Range) from the request, or None when it is not two days in order."""
    from agent_friday.routes.ledger import _when
    from agent_friday.services import evidence_pack
    since, until = str(body.get("since") or ""), str(body.get("until") or "")
    if not (_DAY.match(since) and _DAY.match(until)):
        return None
    start, end = _when(since), _when(until, end=True)
    if start is None or end is None or end <= start or (end - start) > MAX_DAYS * 86400:
        return None
    return since, until, evidence_pack.Range(int(start), int(end))


def _run(approval_id: str, device_id: str, rng) -> None:
    from agent_friday.services import evidence_pack, evidence_pack_export
    try:
        saved = evidence_pack_export.export_to_disk(rng, evidence_pack_export.app_version(), "device:" + device_id)
        done = {"state": "done", "path": saved.path, "filename": saved.filename, "entries": saved.entries,
                "manifest_sha256": saved.manifest_sha256, "recorded": saved.recorded,
                "note": "Saved on this PC. Nothing was sent to your phone."}
    except evidence_pack.PackRefused as refused:
        done = {"state": "failed", "why": str(refused)}
    except Exception as e:  # noqa: BLE001
        _log.warning("evidence pack build failed: %s", type(e).__name__)
        done = {"state": "failed", "why": "The evidence pack could not be made; nothing was exported."}
    with _LOCK:
        _JOBS[approval_id] = done


def _status(rec: dict, dev: dict) -> dict:
    approval_id = str(rec["approval_id"])
    with _LOCK:
        known = _JOBS.get(approval_id)
    if known is not None:
        return known
    word = approvals.lifecycle(rec)
    if word in STATES_OF_A_CARD:
        return {"state": STATES_OF_A_CARD[word]}
    if word == "consumed":
        return {"state": "interrupted", "why": "Friday stopped before the pack was finished; ask again."}
    if word != "approved":
        return {"state": "refused", "why": "that approval cannot start a pack"}
    payload = rec.get("payload") or {}
    shown = _range({"since": payload.get("since"), "until": payload.get("until")})
    if shown is None or not approvals.spend_once(approval_id, "evidence_pack:" + str(dev["id"])):
        return {"state": "refused", "why": "that approval cannot start a pack"}
    with _LOCK:
        _JOBS[approval_id] = {"state": "running"}
    threading.Thread(target=_run, args=(approval_id, str(dev["id"]), shown[2]), name="evidence-pack", daemon=True).start()
    return {"state": "running"}


def register(app: Flask) -> None:
    @app.post("/tv/v1/evidence/pack")
    def pc_evidence_start():
        denied = _guard()
        if denied is not None:
            return denied
        got = _range(request.get_json(silent=True) or {})
        if got is None:
            return refuse(400, "send {since, until} as YYYY-MM-DD, in order, at most %d days apart" % MAX_DAYS, code="bad_request")
        since, until, _ = got
        rec = approvals.create_approval(
            kind=KIND, subject_type=SUBJECT, subject_id="%s..%s" % (since, until),
            title="Export the evidence pack", force_gate=True, requested_by="device:" + str(g.device["id"]),
            description="From %s to %s. The pack is built and saved on this PC only; nothing is sent to the phone." % (since, until),
            action_description="build the evidence pack for %s to %s and save it on this PC" % (since, until),
            payload={"since": since, "until": until, "device_id": str(g.device["id"])})
        resp = jsonify({"ok": True, "approval_id": rec["approval_id"], **_status(rec, g.device)})
        resp.status_code = 202
        return resp

    @app.get("/tv/v1/evidence/pack/<approval_id>")
    def pc_evidence_status(approval_id):
        denied = _guard()
        if denied is not None:
            return denied
        rec = approvals.get_approval(approval_id) if _ID.match(approval_id) else None
        if rec is None or rec.get("kind") != KIND:
            return pc_read._refuse(404, "this PC has no such evidence pack request", code="unknown")
        return jsonify({"ok": True, "approval_id": approval_id, **_status(rec, g.device)})
