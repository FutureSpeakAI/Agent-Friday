"""Which issuers this node has chosen to accept (NS-40.4-1; design A-5, A-6, EC-1). TEST CREDITS ONLY.

The default is none. Accepting an issuer is a press on this PC's own screen; withdrawing needs no
press (it only removes trust). The list is signed by the owner's attestation key on every write and
checked on every read: a list that was edited by hand reads as empty (fail closed), not as "trust
whatever it says".

A peer event that names an issuer this node has not accepted is declined before any of its content
is read: `named_issuer` looks only at the one field where an issuer appears. The wire terms carry no charter digest,
so the ingest check matches on the issuer id only: the charter digest is checked where a journal or a list entry
binds it, not at ingest.

Rules and the tests that hold them (tests/unit/test_accepted_issuers.py):
- Default none: test_by_default_no_issuer_is_accepted.
- Accepting needs a press; a voice, text, bare "owner" label or no press is refused:
  test_accepting_needs_a_press_and_nothing_else_counts.
- Withdrawing needs no press: test_withdrawing_needs_no_press_and_works_from_anywhere.
- An issuer id with another charter digest is not the accepted issuer:
  test_the_same_id_with_another_charter_is_not_accepted.
- An edited list is distrusted: test_a_hand_edited_list_reads_as_empty.
- An event for an unaccepted issuer is declined before its content is read:
  test_an_event_for_an_unaccepted_issuer_is_declined_and_nothing_else_is_parsed (tests/api).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from agent_friday.services import peer_wire_v2 as wire
from agent_friday.services.issuer_registry import IssuerRef

LIST_PREFIX = b"asimov-accepted-issuers/v1" + bytes([0])
ACCEPTED_BY = "owner:press"


@dataclass(frozen=True)
class Press:
    """Proof that this request was a press on this PC's own screen. Only `from_request` makes one."""
    _made_here: bool = True

    @classmethod
    def from_request(cls, request) -> "Press | None":
        from agent_friday.services import screen_click
        return cls() if screen_click.screen_session(request) else None


class AcceptOutcome(str, Enum):
    ACCEPTED = "accepted"
    ALREADY = "already"
    PRESS_REQUIRED = "press-required"
    OTHER_CHARTER = "other-charter"
    CANNOT_SIGN = "cannot-sign"


def named_issuer(raw: bytes) -> str | None:
    """The issuer a job offer names, read from the one field it lives in, or None. It parses the
    canonical bytes and looks no further: no schema, no signature, no other field."""
    try:
        env = wire.decode(raw)
        issuer = env["body"]["payload"]["terms"]["issuer"]
    except (wire.WireError, KeyError, TypeError):
        return None
    return issuer if isinstance(issuer, str) else None


class AcceptedIssuers:
    def __init__(self, path, sign: Callable[[bytes], str], public_key: str):
        self._path = Path(path)
        self._sign = sign
        self._public_key = public_key

    def _read(self) -> list:
        try:
            doc = json.loads(self._path.read_text(encoding="ascii"))
            entries, signature = doc["entries"], doc["signature"]
            body = wire.canonical({"entries": entries})
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(self._public_key)).verify(
                bytes.fromhex(signature), LIST_PREFIX + body)
            return entries
        except (OSError, ValueError, KeyError, TypeError, InvalidSignature, wire.WireError):
            return []

    def _write(self, entries: list) -> None:
        body = wire.canonical({"entries": entries})
        signature = self._sign(LIST_PREFIX + body)
        if not signature:
            raise RuntimeError("the list could not be signed")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps({"entries": entries, "signature": signature}, sort_keys=True), encoding="ascii")
        os.replace(temp, self._path)

    def refs(self) -> list:
        return [IssuerRef(e["issuer_id"], e["charter_digest"], e["unit_code"]) for e in self._read()]

    def is_accepted(self, ref: IssuerRef) -> bool:
        return ref in self.refs()

    def is_accepted_id(self, issuer_id: str) -> bool:
        return any(r.issuer_id == issuer_id for r in self.refs())

    def accept(self, ref: IssuerRef, press, accepted_at: int) -> AcceptOutcome:
        if not isinstance(press, Press):
            return AcceptOutcome.PRESS_REQUIRED
        entries = self._read()
        for entry in entries:
            if entry["issuer_id"] == ref.issuer_id:
                same = entry["charter_digest"] == ref.charter_digest and entry["unit_code"] == ref.unit_code
                return AcceptOutcome.ALREADY if same else AcceptOutcome.OTHER_CHARTER
        entries.append({"issuer_id": ref.issuer_id, "charter_digest": ref.charter_digest,
                        "unit_code": ref.unit_code, "accepted_by": ACCEPTED_BY, "accepted_at": accepted_at})
        try:
            self._write(entries)
        except (RuntimeError, OSError):
            return AcceptOutcome.CANNOT_SIGN        # nothing was written: the list stays as it was
        return AcceptOutcome.ACCEPTED

    def withdraw(self, issuer_id: str) -> bool:
        """Removes an issuer. No press: withdrawing only takes trust away."""
        entries = self._read()
        kept = [e for e in entries if e["issuer_id"] != issuer_id]
        if len(kept) == len(entries):
            return False
        self._write(kept)
        return True
