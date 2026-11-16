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

Each rule above is held by a test in tests/unit/test_tv_app_update.py: no record, a record that is not an object, a
bad name or hash: ``test_no_record_means_no_apk_even_with_files_in_the_folder``,
``test_a_record_that_is_not_an_object_is_no_build_not_a_crash`` and
``test_a_record_that_is_not_a_clear_acceptance_serves_nothing``; changed bytes:
``test_a_build_changed_after_acceptance_is_refused`` and ``test_a_same_size_swap_with_the_same_mtime_is_refused``;
only an owner-bound phone or tablet: ``test_only_an_owner_phone_or_tablet_is_answered``; Range and If-Range:
``test_a_range_resumes_and_is_not_a_second_download`` and ``test_if_range_with_a_stale_validator_sends_the_whole_file``; one receipt per download, not per resumed piece:
``test_a_range_resumes_and_is_not_a_second_download``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from flask import Flask, Response, g, jsonify, request, send_file

_log = logging.getLogger(__name__)

MANIFEST = "LATEST.json"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.apk$")
_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


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


def _sha256(apk_path: Path) -> str:
    """The file's SHA-256, read on every request: a cache keyed on size and mtime would serve swapped bytes under
    the old hash. Guarded by test_a_same_size_swap_with_the_same_mtime_is_refused."""
    digest = hashlib.sha256()
    with open(apk_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class AcceptedBuild:
    file: str
    path: Path
    version: str
    version_code: Optional[int]
    sha256: str
    size: int
    accepted_by: str
    accepted_at: str
    accepted_commit: str

    def accepted(self) -> dict:
        return {"by": self.accepted_by, "at": self.accepted_at, "commit": self.accepted_commit}


def _no_build(reason: str = "this PC has no accepted phone build to offer") -> Tuple[None, Response]:
    return None, _refuse(404, reason, code="no_accepted_build")


def accepted_build() -> Tuple[Optional[AcceptedBuild], Optional[Response]]:
    """(the accepted build, None) or (None, the refusal to send)."""
    folder_path = folder()
    try:
        record = json.loads((folder_path / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _no_build()
    if not isinstance(record, dict):
        return _no_build()
    acceptance = record.get("accepted")
    name, wanted_hash = str(record.get("file") or ""), str(record.get("sha256") or "")
    acceptance_ok = isinstance(acceptance, dict) and acceptance.get("by") and acceptance.get("at")
    if not (acceptance_ok and _NAME.match(name) and _HEX.match(wanted_hash) and record.get("version")):
        return _no_build()
    try:
        real = (folder_path / name).resolve()
        if real.parent != folder_path.resolve() or not real.is_file():
            raise OSError("outside")
        got = _sha256(real)
    except OSError:
        return _no_build("the accepted phone build is not on this PC")
    if got.lower() != wanted_hash.lower():
        _log.warning("phone apk %s no longer matches its accepted hash", name)
        return None, _refuse(503, "the build on this PC changed after it was accepted; it will not be sent",
                             code="apk_changed")
    try:
        version_code = int(record.get("version_code"))
    except (TypeError, ValueError):
        version_code = None
    return AcceptedBuild(file=name, path=real, version=str(record["version"])[:40], version_code=version_code,
                         sha256=got.lower(), size=real.stat().st_size, accepted_by=str(acceptance["by"])[:80],
                         accepted_at=str(acceptance["at"])[:40],
                         accepted_commit=str(acceptance.get("commit") or "")[:40]), None


def _guard() -> Optional[Response]:
    from agent_friday.tv_gateway import pc_read
    if not pc_read.allowed(getattr(g, "device", None)):
        return _refuse(403, "this device cannot take app updates from the PC", code="app_update_not_for_this_device")
    return None


def _audit(what: str, build: AcceptedBuild) -> None:
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import tailnet
    device_pairing._receipt(what, g.device, version=build.version, version_code=build.version_code,
                            sha256=build.sha256, via=tailnet.via())


def register(app: Flask) -> None:
    @app.get("/tv/v1/app/latest")
    def app_latest():
        denied = _guard()
        if denied is not None:
            return denied
        build, refusal = accepted_build()
        if refusal is not None:
            return refusal
        out = {"ok": True, "version": build.version, "version_code": build.version_code, "sha256": build.sha256,
               "size": build.size, "accepted": build.accepted(), "apk": "/tv/v1/app/apk"}
        have = request.args.get("have", "")
        if have.isdigit() and build.version_code is not None:
            out["up_to_date"] = int(have) >= build.version_code
        return jsonify(out)

    @app.get("/tv/v1/app/apk")
    def app_apk():
        denied = _guard()
        if denied is not None:
            return denied
        build, refusal = accepted_build()
        if refusal is not None:
            return refusal
        if not request.headers.get("Range"):
            _audit("device_app_download", build)
        resp = send_file(build.path, mimetype="application/vnd.android.package-archive", conditional=True,
                         as_attachment=True, download_name=build.file, max_age=0)
        resp.headers["X-Friday-SHA256"] = build.sha256
        resp.headers["X-Friday-Version"] = build.version
        resp.headers["Cache-Control"] = "no-store"
        return resp
