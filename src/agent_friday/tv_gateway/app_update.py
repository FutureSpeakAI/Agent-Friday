"""Update the phone app from this PC (owner devices only, over the LAN or the tailnet).

* GET /tv/v1/app/latest : the newest APK this PC holds that has PASSED acceptance (version, versionCode, SHA-256,
  size, who accepted it and when). ``?have=<versionCode>`` adds ``up_to_date``.
* GET /tv/v1/app/apk    : that APK, with the SHA-256 in ``X-Friday-SHA256``. Range and If-Range work, so a download
  over cellular can resume.

What counts as "passed acceptance" is a record, never a guess: ``LATEST.json`` in the APK folder, written by whoever
ran the acceptance (the phone lane or the release lead), naming ONE file and carrying ``accepted`` = {by, at} plus
its ``sha256``. The folder is never listed: a build that is not named there is never served, and a file whose bytes no
longer match the recorded hash is refused (503 ``apk_changed``) instead of being sent. No record, no APK: 404
``no_accepted_build``.

Folder: ``FRIDAY_PHONE_APK_DIR``, else ``<friday home>/phone_apk``. Only an owner-bound phone or tablet is answered
(same binding as the read door; a watch, TV, family or unbound device is refused). Nothing is installed by this route,
and an APK is only ever bytes: Android's own installer and signature check still decide.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Optional, Tuple

from flask import Flask, Response, g, jsonify, request, send_file

_log = logging.getLogger(__name__)

MANIFEST = "LATEST.json"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.apk$")
_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_HASHED: dict = {}                      # (path, size, mtime_ns) -> sha256
_LOCK = threading.Lock()


def _refuse(status: int, reason: str, **extra) -> Response:
    resp = jsonify({"ok": False, "error": reason, **extra})
    resp.status_code = status
    return resp


def folder() -> Path:
    env = os.environ.get("FRIDAY_PHONE_APK_DIR")
    if env:
        return Path(env).expanduser()
    from agent_friday.paths import friday_home
    return friday_home() / "phone_apk"


def _sha256(p: Path) -> str:
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns)
    with _LOCK:
        if key in _HASHED:
            return _HASHED[key]
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    with _LOCK:
        _HASHED.clear()
        _HASHED[key] = h.hexdigest()
    return _HASHED[key]


def accepted_build() -> Tuple[Optional[dict], Optional[Response]]:
    """(the accepted build, None) or (None, the refusal to send)."""
    d = folder()
    try:
        rec = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, _refuse(404, "this PC has no accepted phone build to offer", code="no_accepted_build")
    acc = rec.get("accepted") if isinstance(rec, dict) else None
    name, want = str(rec.get("file") or ""), str(rec.get("sha256") or "")
    ok_acc = isinstance(acc, dict) and acc.get("by") and acc.get("at")
    if not (ok_acc and _NAME.match(name) and _HEX.match(want) and rec.get("version")):
        return None, _refuse(404, "this PC has no accepted phone build to offer", code="no_accepted_build")
    p = (d / name)
    try:
        real = p.resolve()
        if real.parent != d.resolve() or not real.is_file():
            raise OSError("outside")
        got = _sha256(real)
    except OSError:
        return None, _refuse(404, "the accepted phone build is not on this PC", code="no_accepted_build")
    if got.lower() != want.lower():
        _log.warning("phone apk %s no longer matches its accepted hash", name)
        return None, _refuse(503, "the build on this PC changed after it was accepted; it will not be sent",
                             code="apk_changed")
    try:
        vc = int(rec.get("version_code"))
    except (TypeError, ValueError):
        vc = None
    return {"file": name, "path": real, "version": str(rec["version"])[:40], "version_code": vc,
            "sha256": got.lower(), "size": real.stat().st_size,
            "accepted": {"by": str(acc["by"])[:80], "at": str(acc["at"])[:40],
                         "commit": str(acc.get("commit") or "")[:40]}}, None


def _guard() -> Optional[Response]:
    from agent_friday.tv_gateway import pc_read
    if not pc_read.allowed(getattr(g, "device", None)):
        return _refuse(403, "this device cannot take app updates from the PC", code="app_update_not_for_this_device")
    return None


def _audit(what: str, build: dict) -> None:
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import tailnet
    device_pairing._receipt(what, g.device, version=build["version"], version_code=build["version_code"],
                            sha256=build["sha256"], via=tailnet.via())


def register(app: Flask) -> None:
    @app.get("/tv/v1/app/latest")
    def app_latest():
        denied = _guard()
        if denied is not None:
            return denied
        b, refusal = accepted_build()
        if refusal is not None:
            return refusal
        out = {"ok": True, "version": b["version"], "version_code": b["version_code"], "sha256": b["sha256"],
               "size": b["size"], "accepted": b["accepted"], "apk": "/tv/v1/app/apk"}
        have = request.args.get("have", "")
        if have.isdigit() and b["version_code"] is not None:
            out["up_to_date"] = int(have) >= b["version_code"]
        return jsonify(out)

    @app.get("/tv/v1/app/apk")
    def app_apk():
        denied = _guard()
        if denied is not None:
            return denied
        b, refusal = accepted_build()
        if refusal is not None:
            return refusal
        if not request.headers.get("Range"):
            _audit("device_app_download", b)             # one row per download, not per resumed piece
        resp = send_file(b["path"], mimetype="application/vnd.android.package-archive", conditional=True,
                         as_attachment=True, download_name=b["file"], max_age=0)
        resp.headers["X-Friday-SHA256"] = b["sha256"]
        resp.headers["X-Friday-Version"] = b["version"]
        resp.headers["Cache-Control"] = "no-store"
        return resp
