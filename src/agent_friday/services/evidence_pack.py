"""The evidence pack: one local zip of what Friday recorded for a date range (brief K6).

Entries in, files out. This module reads nothing from disk and reaches no network (held by
test_the_pack_modules_import_nothing_that_reaches_a_network). The caller passes the whole
ledger in order, the approvals, the grants and the ledger's own verify result.

Rules and the tests that hold them (tests/unit/test_evidence_pack.py):
- The pack holds exactly the stored entries in range, in order, with the true starting hash:
  test_the_pack_holds_exactly_the_entries_in_range_for_every_small_ledger.
- It is the full range or a refusal, never a short pack: test_a_range_over_the_old_export_cap_
  is_written_whole.
- A third party can check it with the standard library: test_a_clean_interpreter_with_only_
  the_standard_library_passes_the_check.
- Any changed, removed, reordered or duplicated part is named: test_changing_a_file_*,
  test_removing_or_reordering_*.
- No prompt, result or message text: test_content_fields_in_the_ledger_refuse_the_pack and
  test_the_summary_and_approvals_carry_no_free_text.
- The summary numbers are recounted from the pack's own files: test_summary_numbers_equal_a_
  recount_of_the_pack_files.
- A break in range is stated first: test_a_break_is_the_first_line_of_the_summary.
- The manifest is signed over the bytes of Federation's canonical form, not a new one:
  test_the_manifest_bytes_come_from_the_federation_canonical_function.
- Change the manifest, or sign with another key: the check fails:
  test_a_changed_manifest_fails_the_signature, test_another_keys_signature_fails.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping

from agent_friday.services import ledger_text
from agent_friday.services import peer_wire_v2 as wire

GENESIS = "0" * 64
MANIFEST_PREFIX = b"asimov-evidence-pack/v1/manifest\x00"
SUPPORTS_SENTENCE = ("This export supports ISO 27001 evidence-gathering. "
                     "It is not a certification and makes no compliance claim.")
CONTENT_KEYS = frozenset({"prompt", "result", "message", "text", "content", "body", "reply",
                          "transcript", "description"})
APPROVAL_FIELDS = ("approval_id", "kind", "subject_type", "policy_class", "gated", "status",
                   "consumed", "created_at", "expires_at", "decided_at", "decided_by",
                   "decided_source", "decided_check", "used_at", "off_record", "policy_version",
                   "decided_policy_version", "action_fingerprint", "decided_device", "withdrawn")
# The fields a real grant row carries (governance/action_gate.create_grant and _ledger_row): labels, numbers and
# times only. No free text (note, scope, pattern) is copied. Held by test_a_grant_row_carries_only_its_listed_fields.
GRANT_FIELDS = ("grant_id", "tools", "created_at", "expires_at", "max_uses", "uses_left", "used_count",
                "first_used_at", "revoked", "money_spent", "policy_version", "usage_read")
APPROVAL_TIMES = ("created_at", "decided_at", "used_at")
GRANT_TIMES = ("created_at", "first_used_at", "expires_at")   # no revocation time: a grant row does not carry a real one


class PackFile(str, Enum):
    SUMMARY = "SUMMARY.md"
    LEDGER = "ledger.jsonl"
    APPROVALS = "approvals.jsonl"
    GRANTS = "grants.jsonl"
    VERIFY = "verify.json"
    HOW_TO_CHECK = "HOW_TO_CHECK.md"
    CHECKER = "check_pack.py"
    MANIFEST = "manifest.json"
    SIGNATURE = "manifest.sig"


class PackRefused(ValueError):
    """The pack cannot be written whole and honest; the message says why in plain words."""


@dataclass(frozen=True)
class Range:
    """Entries with `start <= t < end`, whole unix seconds."""
    start: int
    end: int

    def __post_init__(self):
        if type(self.start) is not int or type(self.end) is not int or not 0 <= self.start < self.end:
            raise PackRefused("The range must end after it starts.")

    def holds(self, entry: Mapping) -> bool:
        return self.start <= entry["t"] < self.end


@dataclass(frozen=True)
class Signer:
    """Signs bytes with the owner's attestation key; `sign` returns hex or None (no key)."""
    sign: Callable[[bytes], str | None]
    public_key: str


@dataclass(frozen=True)
class Pack:
    files: Mapping[str, bytes]
    manifest: Mapping

    def zip_bytes(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(self.files):
                z.writestr(zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0)), self.files[name])
        return out.getvalue()


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _jsonl(rows: Iterable[Mapping]) -> bytes:
    return b"".join(canon(row) + b"\n" for row in rows)


def _refuse_content(entries: list[Mapping]) -> None:
    for entry in entries:
        found = _content_key(entry.get("rec"))
        if found:
            raise PackRefused(
                f"Entry {entry['seq']} carries free text ('{found}'). The pack holds labels and "
                "hashes only, so nothing was written.")
        raw = ledger_text.raw_text_key(entry.get("rec"))
        if raw:
            raise PackRefused(
                f"Entry {entry['seq']} was written before free text was kept as a hash ('{raw}' holds the "
                "text itself). The pack holds labels and hashes only, so nothing was written; "
                "choose a range that starts after that entry.")


def _content_key(node) -> str | None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in CONTENT_KEYS and isinstance(value, str) and value:
                return key
            nested = _content_key(value)
            if nested:
                return nested
    elif isinstance(node, list):
        for item in node:
            nested = _content_key(item)
            if nested:
                return nested
    return None


def _pick(rows: Iterable[Mapping], fields: tuple[str, ...]) -> list[dict]:
    return [{k: row[k] for k in fields if k in row} for row in rows]


def _in_range(rows: Iterable[Mapping], keys: tuple[str, ...], rng: Range) -> list[Mapping]:
    """Rows with any of `keys` inside the range. A row with no time at all cannot be placed, so
    it is kept (the pack is never silently short) and the summary counts it."""
    chosen = []
    for row in rows:
        if row.get("usage_read") is False:     # its use is unknown, so it cannot be placed by a use or revocation time
            chosen.append(row)
            continue
        times = [row[k] for k in keys if isinstance(row.get(k), (int, float))]
        if not times or any(rng.start <= t < rng.end for t in times):
            chosen.append(row)
    return chosen


def _undated(rows: Iterable[Mapping], keys: tuple[str, ...]) -> int:
    return sum(1 for r in rows if not any(isinstance(r.get(k), (int, float)) for k in keys))


def _starting_hash(entries: list[Mapping], chosen: list[Mapping]) -> str:
    if not chosen:
        return GENESIS
    before = [e for e in entries if e["seq"] < chosen[0]["seq"]]
    return before[-1]["hash"] if before else GENESIS


def build_pack(entries: list[Mapping], approvals: Iterable[Mapping], grants: Iterable[Mapping],
               verify_result: Mapping, rng: Range, app_version: str, exported_at: int,
               signer: Signer | None = None) -> Pack:
    chosen = [e for e in entries if rng.holds(e)]
    _refuse_content(chosen)
    approval_rows = _pick(_in_range(approvals, APPROVAL_TIMES, rng), APPROVAL_FIELDS)
    grant_rows = _pick(_in_range(grants, GRANT_TIMES, rng), GRANT_FIELDS)
    checker = Path(__file__).with_name("evidence_pack_checker.py").read_bytes()
    body = {
        PackFile.LEDGER.value: _jsonl(chosen),
        PackFile.APPROVALS.value: _jsonl(approval_rows),
        PackFile.GRANTS.value: _jsonl(grant_rows),
        PackFile.VERIFY.value: canon(dict(verify_result)) + b"\n",
        PackFile.CHECKER.value: checker,
        PackFile.HOW_TO_CHECK.value: how_to_check().encode("utf-8"),
    }
    body[PackFile.SUMMARY.value] = summary(chosen, approval_rows, grant_rows, verify_result,
                                           rng).encode("utf-8")
    key_id = _sha(bytes.fromhex(signer.public_key))[:16] if signer else None
    manifest = {
        "range": {"start": rng.start, "end": rng.end},
        "entries": len(chosen),
        "first_seq": chosen[0]["seq"] if chosen else None,
        "last_seq": chosen[-1]["seq"] if chosen else None,
        "prev_hash": _starting_hash(entries, chosen),
        "head_hash": chosen[-1]["hash"] if chosen else None,
        "kinds": _counts(e["kind"] for e in chosen),
        "policy_versions": sorted({e["pv"] for e in chosen if "pv" in e}),
        "app_version": app_version,
        "exported_at": exported_at,
        "ledger_verified": bool(verify_result.get("ok")),
        "public_key": signer.public_key if signer else None,
        "key_id": key_id,
        "files": {name: _sha(data) for name, data in sorted(body.items())},
    }
    files = dict(body)
    manifest_bytes = wire.canonical(manifest)
    files[PackFile.MANIFEST.value] = manifest_bytes
    signature = signer.sign(MANIFEST_PREFIX + manifest_bytes) if signer else None
    if signature:
        files[PackFile.SIGNATURE.value] = signature.encode("ascii")
    return Pack(files, manifest)


def _counts(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


def how_to_check() -> str:
    return (
        "# How to check this pack\n\n"
        "Unpack the zip, then run `python check_pack.py <the zip or the folder>`. It needs only "
        "Python's standard library, no Friday and no key of the owner's.\n\n"
        "It recomputes the SHA-256 of every file the manifest lists and walks the hash chain over "
        "`ledger.jsonl` from the starting hash the manifest states.\n\n"
        "What this shows: the files are what was exported and the entries are in one unbroken "
        "chain. What it does not show: that the PC was honest when the entries were written. "
        "Each entry's `sig` is an HMAC under a key that stays on the owner's PC, so an outsider "
        "cannot check it.\n\n"
        "The pack holds labels and hashes only, never prompts, results or message text.\n")


def summary(entries: list[Mapping], approvals: list[Mapping], grants: list[Mapping],
            verify_result: Mapping, rng: Range) -> str:
    lines = ["# Evidence pack summary", ""]
    if not verify_result.get("ok"):
        lines += [f"**The ledger check found a break: {verify_result.get('first_break', 'see verify.json')}**",
                  ""]
    kinds = _counts(e["kind"] for e in entries)
    status = _counts(str(a.get("status")) for a in approvals)
    source = _counts(str(a.get("decided_source")) for a in approvals if a.get("decided_source"))
    versions = sorted({e["pv"] for e in entries if "pv" in e})
    undated = _undated(approvals, APPROVAL_TIMES) + _undated(grants, GRANT_TIMES)
    unread = sum(1 for g in grants if g.get("usage_read") is False)
    lines += [
        f"Range: {rng.start} to {rng.end} (unix seconds, start included, end excluded).",
        f"Ledger entries: {len(entries)}",
        *[f"- {kind}: {count}" for kind, count in kinds.items()],
        f"Approvals: {len(approvals)}",
        *[f"- status {name}: {count}" for name, count in status.items()],
        *[f"- decided from {name}: {count}" for name, count in source.items()],
        f"Grants: {len(grants)}",
        "Grants listed are those still on file: a grant revoked or expired and then pruned is not listed (the ledger keeps only its revoke line).",
        *([f"Grants whose use was not read (the ledger could not be read): {unread}"] if unread else []),
        *([f"Rows with no recorded time (kept): {undated}"] if undated else []),
        "Actions seen for the first time: "
        + ("recorded" if any("novelty" in a for a in approvals) else "the label was not recorded"),
        f"Policy versions in force: {', '.join(versions) if versions else 'none recorded'}",
        "Chain: " + ("intact" if verify_result.get("ok") else "BROKEN, see the first line"),
        "", SUPPORTS_SENTENCE, "",
        "| File | Control themes it is evidence for |", "|---|---|",
        "| ledger.jsonl | logging, monitoring, collection of evidence |",
        "| approvals.jsonl | access and authentication, privileged access, change approval |",
        "| grants.jsonl | privileged access, change approval |",
        "| verify.json, manifest.json | collection of evidence |", ""]
    return "\n".join(lines)
