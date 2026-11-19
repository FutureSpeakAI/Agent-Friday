"""The phone's own update contract (friday-phone docs/PC-UPDATE.md): GET /tv/v1/phone/update and /apk?vc=N.

The same accepted build as /tv/v1/app/latest (LATEST.json with an acceptance record, never a folder listing, bytes
re-hashed on every request), answered in the shape the phone app asks for. Behind the same gate as the read door:
an owner phone or tablet holding ``pc_read``; a device without it raises that card and gets 403 ``needs_right``.

* ``/phone/update`` is 200 either way: ``available: true`` with package, form (phone or tablet), version code and name,
  SHA-256, size, the signing certificate's SHA-256, ``acceptance: "passed"`` and ``built_at``; or ``available: false``
  when nothing is staged, the record lacks any of those fields, or the file is over 200 MB. A file whose bytes changed
  after acceptance is a 503 ``apk_changed``.
* ``/phone/update/apk?vc=N`` sends the staged APK only when N is its version code (404 otherwise), with
  Content-Length, no host path in any answer, and the same one-receipt-per-download rule as /tv/v1/app/apk.
Each rule is held by a test in tests/unit/test_tv_phone_update.py.
"""
from __future__ import annotations

import re

from flask import Flask, g, jsonify, request, send_file

from agent_friday.tv_gateway import app_update, pc_read

refuse = pc_read._refuse

MAX_APK_BYTES = 200 * 1024 * 1024
APK_MIME = "application/vnd.android.package-archive"
_VERSION_CODE = re.compile(r"[0-9]{1,10}")


def _staged() -> tuple:
    """(build, None) for an offerable build, (None, None) for nothing to offer, (None, refusal) for apk_changed.
    The record is the one for the asking device's own form (phone or tablet)."""
    kind = str(g.device.get("kind") or "").lower()
    build, refusal = app_update.accepted_build(kind)
    if refusal is not None:
        return None, (refusal if refusal.status_code == 503 else None)
    if build.phone is None or build.version_code is None or not 0 < build.size <= MAX_APK_BYTES:
        return None, None
    if build.phone.form != kind:      # a record for the other form is never offered (the phone would refuse it)
        return None, None
    return build, None


def register(app: Flask) -> None:
    @app.get("/tv/v1/phone/update")
    def phone_update():
        denied = pc_read._guard("phone_update")
        if denied is not None:
            return denied
        build, refusal = _staged()
        if refusal is not None:
            return refusal
        if build is None:
            return jsonify({"ok": True, "available": False})
        facts = build.phone
        return jsonify({"ok": True, "available": True, "package": facts.package, "form": facts.form,
                        "version_code": build.version_code, "version_name": facts.version_name,
                        "sha256": build.sha256, "size": build.size, "cert_sha256": facts.cert_sha256,
                        "acceptance": "passed", "built_at": facts.built_at})

    @app.get("/tv/v1/phone/update/apk")
    def phone_update_apk():
        denied = pc_read._guard("phone_update")
        if denied is not None:
            return denied
        build, refusal = _staged()
        if refusal is not None:
            return refusal
        wanted = request.args.get("vc", "")
        if build is None or not _VERSION_CODE.fullmatch(wanted) or int(wanted) != build.version_code:
            return refuse(404, "that is not the build this PC has staged", code="not_the_staged_build")
        resp = send_file(build.path, mimetype=APK_MIME, conditional=True, as_attachment=True,
                         download_name=build.file, max_age=0)
        if app_update.is_download_start(resp):
            app_update.audit_download("device_app_download", build)
        resp.headers["X-Friday-SHA256"] = build.sha256
        resp.headers["Cache-Control"] = "no-store"
        return resp
