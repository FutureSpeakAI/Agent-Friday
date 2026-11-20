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
``test_a_range_resumes_and_is_not_a_second_download`` and
``test_if_range_with_a_stale_validator_sends_the_whole_file``; one receipt per download that starts at byte 0 (a whole
file, or a range from 0), none for a resumed piece: ``test_a_range_resumes_and_is_not_a_second_download``,
``test_a_range_from_byte_zero_is_a_download``, ``test_a_stale_if_range_whole_file_is_a_download``,
``test_a_head_or_a_one_byte_probe_is_not_a_download``.
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
#: One record per form, so a phone build and a tablet build can both be staged: ``LATEST-phone.json``,
#: ``LATEST-tablet.json``. A device is offered the record for its own kind (phone or tablet), never the other's.
FORM_MANIFEST = "LATEST-{form}.json"
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.apk$")
_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
FORMS = ("phone", "tablet")


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
class PhoneFacts:
    """What the phone's own update check needs beyond the accepted build (docs/PC-UPDATE.md in friday-phone)."""
    package: str
    form: str
    version_name: str
    cert_sha256: str
    built_at: int


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
    phone: Optional[PhoneFacts] = None

    def accepted(self) -> dict:
        return {"by": self.accepted_by, "at": self.accepted_at, "commit": self.accepted_commit}


def _phone_facts(record: dict) -> Optional[PhoneFacts]:
    """The extra fields of the phone's contract, or None when any is missing or malformed (then no phone offer)."""
    package, form = str(record.get("package") or ""), str(record.get("form") or "")
    cert, name = str(record.get("cert_sha256") or ""), str(record.get("version_name") or "")
    built_at = record.get("built_at")
    if not (_PACKAGE.match(package) and form in FORMS and _HEX.match(cert) and 0 < len(name) <= 40
            and isinstance(built_at, int) and not isinstance(built_at, bool) and built_at > 0):
        return None
    return PhoneFacts(package, form, name, cert.lower(), built_at)


def _no_build(reason: str = "this PC has no accepted phone build to offer") -> Tuple[None, Response]:
    return None, _refuse(404, reason, code="no_accepted_build")


def _record_for(folder_path: Path, form: Optional[str]):
    """The parsed record a device of this form is offered, or None. With a form: LATEST-<form>.json when it exists,
    else LATEST.json (the phone route then offers it only if it names the device's own form). Without a form (the
    app/ alias routes): LATEST.json. Guarded by tests/unit/test_tv_phone_update.py (the per-form cases)."""
    if form in FORMS:
        names = [FORM_MANIFEST.format(form=form), MANIFEST]
    else:
        names = [MANIFEST]
    for name in names:
        try:
            text = (folder_path / name).read_text(encoding="utf-8")
        except OSError:
            continue                      # no such file: try the next name
        try:
            return json.loads(text)
        except ValueError:
            return None                   # a record that is there but unreadable offers nothing, not an older build
    return None


def accepted_build(form: Optional[str] = None) -> Tuple[Optional[AcceptedBuild], Optional[Response]]:
    """(the accepted build, None) or (None, the refusal to send). ``form`` is the asking device's kind."""
    folder_path = folder()
    record = _record_for(folder_path, form)
    if record is None:
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
    if version_code is not None and version_code <= 0:
        version_code = None
    return AcceptedBuild(file=name, path=real, version=str(record["version"])[:40], version_code=version_code,
                         sha256=got.lower(), size=real.stat().st_size, accepted_by=str(acceptance["by"])[:80],
                         accepted_at=str(acceptance["at"])[:40],
                         accepted_commit=str(acceptance.get("commit") or "")[:40],
                         phone=_phone_facts(record)), None


def _guard() -> Optional[Response]:
    from agent_friday.tv_gateway import pc_read
    if not pc_read.allowed(getattr(g, "device", None)):
        return _refuse(403, "this device cannot take app updates from the PC", code="app_update_not_for_this_device")
    return None


def audit_download(what: str, build: AcceptedBuild) -> None:
    from agent_friday.services import device_pairing
    from agent_friday.tv_gateway import tailnet
    device_pairing._receipt(what, g.device, version=build.version, version_code=build.version_code,
                            sha256=build.sha256, via=tailnet.via())


def is_download_start(resp: Response) -> bool:
    """A download starts with a GET whose bytes begin at the first byte: a whole 200, or a 206 from byte 0. A HEAD
    sends nothing and a one-byte probe (``bytes=0-0``) only asks the size, so neither counts."""
    if request.method != "GET":
        return False
    return resp.status_code == 200 or (resp.headers.get("Content-Range", "").startswith("bytes 0-")
                                       and not resp.headers.get("Content-Range", "").startswith("bytes 0-0/"))


def register(app: Flask) -> None:
    from agent_friday.tv_gateway import phone_update
    phone_update.register(app)
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
        resp = send_file(build.path, mimetype="application/vnd.android.package-archive", conditional=True,
                         as_attachment=True, download_name=build.file, max_age=0)
        if is_download_start(resp):
            audit_download("device_app_download", build)
        resp.headers["X-Friday-SHA256"] = build.sha256
        resp.headers["X-Friday-Version"] = build.version
        resp.headers["Cache-Control"] = "no-store"
        return resp
