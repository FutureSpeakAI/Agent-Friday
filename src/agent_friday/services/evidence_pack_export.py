"""Make the owner's evidence pack from this PC's real records (brief K6, PR C).

Reads the whole action ledger (the archive too, strictly), the approvals and the grants; builds
the pack with services/evidence_pack; signs the manifest with the owner's attestation key; and
writes ONE ledger line saying an export was made. Nothing leaves the PC: the caller returns the
zip to the owner.

Rules and the tests that hold them (tests/api/test_evidence_pack_route.py):
- An unreadable archive writes nothing: test_an_unreadable_archive_writes_nothing.
- A break in range still gives a pack, with the break first: test_a_break_in_range_still_gives_
  a_pack_with_the_break_first.
- The export is itself on the record and the ledger still verifies:
  test_the_export_writes_one_ledger_line_with_the_manifest_hash.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from agent_friday.services import action_ledger, approvals
from agent_friday.services import evidence_pack as pack


@dataclass(frozen=True)
class Exported:
    filename: str
    zip_bytes: bytes
    manifest_sha256: str
    recorded: bool = True


def _signer() -> pack.Signer | None:
    from agent_friday.governance.proof_of_integrity import get_integrity_engine
    engine = get_integrity_engine()
    public = engine.get_public_key_hex()
    return pack.Signer(engine.sign_payload, public) if public else None


def _read_ledger() -> list[dict]:
    try:
        return action_ledger.entries(None, newest_first=False, archive=True, strict=True,
                                     gate=False)
    except action_ledger.LedgerError as err:
        raise pack.PackRefused(
            f"The archive could not be read ({err.user_message}); nothing was exported.") from err


def app_version() -> str:
    from importlib import metadata
    try:
        return metadata.version("agent-friday")
    except metadata.PackageNotFoundError:
        return "unknown"


def _grant_rows(action_gate) -> list:
    try:
        return action_gate.ledger_grant_rows()
    except Exception as err:  # noqa: BLE001 - the key or the ledger could not be read
        raise pack.PackRefused("The grants could not be read (the ledger may have a break to review first), "
                               "so nothing was exported.") from err


def export(rng: pack.Range, version: str, now: int | None = None) -> Exported:
    from agent_friday.governance import action_gate
    moment = int(time.time()) if now is None else now
    entries = _read_ledger()
    built = pack.build_pack(entries, approvals.list_approvals(), _grant_rows(action_gate),
                            action_ledger.verify(force=True), rng, version, moment,
                            _signer())
    manifest_sha = hashlib.sha256(built.files[pack.PackFile.MANIFEST.value]).hexdigest()
    try:
        action_ledger.append("export", {
            "event": "evidence_pack_exported", "by": "owner", "range_start": rng.start,
            "range_end": rng.end, "entries": built.manifest["entries"],
            "manifest_sha256": manifest_sha, "signed": bool(built.manifest["public_key"])})
        recorded = True
    except (action_ledger.LedgerError, OSError):
        recorded = False    # a ledger that cannot take a line (for example one with a break) still gives its pack
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(moment))
    return Exported(f"friday-evidence-{rng.start}_{rng.end}-{stamp}.zip", built.zip_bytes(),
                    manifest_sha, recorded)
