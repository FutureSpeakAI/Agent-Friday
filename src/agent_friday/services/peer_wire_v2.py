"""The signed peer-event v2 wire (Federation / Economy spec, protocol section; roadmap F3/F4).

Pure and offline: bytes in, a verdict out. Nothing here opens a socket, reads a wallet or
runs an effect. It proves AUTHENTICATION only: the bytes are canonical, match the closed
schema, carry a valid Ed25519 signature from a key the caller already trusts, and have a
coherent header and (for an offer) coherent terms. Authorization, evidence, grants,
balances and finality are separate and stay with the local action path; a verified event
is evidence or a request, never a permission.

Wire form (spec): canonical JSON, at most 65,536 bytes, depth at most 24, printable ASCII
only, nonnegative integers up to 2**53-1, no duplicate keys, no fractions or exponents, keys
sorted, no whitespace. The signature is Ed25519 over b"asimov-peer/v2/event\\0" + canonical
body; the event ID is the SHA-256 of those same bytes. The terms hash is SHA-256 over
b"asimov-terms/v2/terms\\0" + canonical terms. The v1 `shannon-agreement` verifier is not
touched by this module.

`PeerEventStore` keeps (realm, actor, action_id) unique: a retry of the same bytes is a
replay of the recorded result, a different event under the same identity is recorded as
equivocation and never replaces the first.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, NamedTuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

EVENT_PREFIX = b"asimov-peer/v2/event\x00"
TERMS_PREFIX = b"asimov-terms/v2/terms\x00"
MAX_BYTES = 65536
MAX_DEPTH = 24
MAX_INT = 2 ** 53 - 1
_SCHEMA_FILE = Path(__file__).resolve().parent.parent / "resources" / "federation" / "peer-event-v2.schema.json"

_SUPPORTED = frozenset({
    "$schema", "title", "type", "enum", "const", "oneOf", "allOf", "if", "then", "properties",
    "required", "additionalProperties", "items", "maxItems", "minItems", "uniqueItems",
    "minimum", "maximum", "minLength", "maxLength", "pattern"})
_TYPES = {"null": type(None), "boolean": bool, "object": dict, "array": list,
          "integer": int, "string": str}


class WireError(ValueError):
    """A peer event that must not be acted on; `code` names why (a closed vocabulary)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Verified(NamedTuple):
    event_id: str
    body: dict
    signature: str


def _fail(code: str):
    raise WireError(code)


def _pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            _fail("duplicate_key")
        out[key] = value
    return out


def _check_tree(item, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        _fail("depth")
    if type(item) is str:
        if any(not 32 <= ord(c) <= 126 for c in item):
            _fail("string")
    elif type(item) is int:
        if not 0 <= item <= MAX_INT:
            _fail("integer")
    elif type(item) is list:
        for v in item:
            _check_tree(v, depth + 1)
    elif type(item) is dict:
        for k, v in item.items():
            _check_tree(k, depth + 1)
            _check_tree(v, depth + 1)
    elif item is not None and type(item) is not bool:
        _fail("type")


def canonical(item: Any) -> bytes:
    """The one byte form that is signed and hashed. Raises WireError for anything outside the profile."""
    _check_tree(item)
    return json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def decode(raw: bytes) -> Any:
    """Parse wire bytes, requiring them to be exactly the canonical form of what they say."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_BYTES:
        _fail("size")
    try:
        parsed = json.loads(bytes(raw).decode("ascii"), object_pairs_hook=_pairs,
                            parse_float=lambda _: _fail("number"),
                            parse_constant=lambda _: _fail("number"))
        if canonical(parsed) != bytes(raw):
            _fail("noncanonical")
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        _fail("encoding")
    return parsed


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def event_id(body: dict) -> str:
    return _digest(EVENT_PREFIX + canonical(body))


def terms_hash(terms: dict) -> str:
    return _digest(TERMS_PREFIX + canonical(terms))


def schema_matches(value: Any, rule: dict) -> bool:
    """Evaluate the closed subset of JSON Schema the peer schemas use. An unknown keyword
    raises instead of silently passing."""
    if set(rule) - _SUPPORTED:
        raise RuntimeError("unsupported schema keyword: " + ", ".join(sorted(set(rule) - _SUPPORTED)))
    if "oneOf" in rule and sum(schema_matches(value, r) for r in rule["oneOf"]) != 1:
        return False
    if "allOf" in rule and not all(schema_matches(value, r) for r in rule["allOf"]):
        return False
    if "if" in rule and schema_matches(value, rule["if"]) and not schema_matches(value, rule["then"]):
        return False
    if "const" in rule and (type(value) is not type(rule["const"]) or value != rule["const"]):
        return False
    if "enum" in rule and not any(type(value) is type(x) and value == x for x in rule["enum"]):
        return False
    if "type" in rule and type(value) is not _TYPES[rule["type"]]:
        return False
    if isinstance(value, dict):
        fields = rule.get("properties", {})
        if not set(rule.get("required", ())) <= set(value):
            return False
        if rule.get("additionalProperties") is False and set(value) - fields.keys():
            return False
        return all(schema_matches(value[k], fields[k]) for k in fields.keys() & value)
    if isinstance(value, list):
        if not rule.get("minItems", 0) <= len(value) <= rule.get("maxItems", len(value)):
            return False
        if rule.get("uniqueItems") and len({canonical(v) for v in value}) != len(value):
            return False
        return all(schema_matches(v, rule.get("items", {})) for v in value)
    if isinstance(value, str):
        return (rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", len(value))
                and ("pattern" not in rule or re.fullmatch(rule["pattern"], value) is not None))
    if type(value) is int:
        return rule.get("minimum", value) <= value <= rule.get("maximum", value)
    return True


_schema_cache: dict | None = None


def event_schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))
    return _schema_cache


def verify_event(raw: bytes, trusted_keys) -> Verified:
    """Authenticate one wire event. `trusted_keys` is the set of actor public keys (64 hex
    characters) the caller already trusts for this realm; an unknown actor is refused.
    Raises WireError(code) with code in: size, encoding, duplicate_key, number, string,
    integer, depth, type, noncanonical, schema, untrusted_key, signature, header,
    terms_mismatch, role, split. Returning means authenticated bytes, nothing more."""
    env = decode(raw)
    if not isinstance(env, dict) or not schema_matches(env, event_schema()):
        _fail("schema")
    body = env["body"]
    if body["actor"] not in set(trusted_keys):
        _fail("untrusted_key")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(body["actor"])).verify(
            bytes.fromhex(env["signature"]), EVENT_PREFIX + canonical(body))
    except (InvalidSignature, ValueError):
        _fail("signature")
    if body["issued_at"] >= body["expires_at"] or body["parents"] != sorted(body["parents"]):
        _fail("header")
    if body["type"] == "job.offer":
        terms = body["payload"]["terms"]
        if terms_hash(terms) != body["terms_hash"] or any(terms[k] != body[k] for k in ("realm", "job", "stage")):
            _fail("terms_mismatch")
        roles = terms["participants"]
        if (roles["requester"] != body["actor"] or roles["provider"] != body["recipient"]
                or roles["provider"] == body["actor"]):
            _fail("role")
        if sum(s["units"] for s in terms["shares"]) != terms["gross_units"]:
            _fail("split")
    return Verified(event_id(body), body, env["signature"])


def effects_allowed_now(body: dict, now: float | None = None) -> bool:
    """New effects need issued_at <= now < expires_at on a trusted local clock. A delayed
    copy stays evidence but is not a request to act (spec: inclusive at issuance, exclusive at expiry)."""
    t = time.time() if now is None else now
    return body["issued_at"] <= t < body["expires_at"]


class PeerEventStore:
    """Durable idempotency for peer events: (realm, actor, action_id) is unique."""

    def __init__(self, path):
        self._path = str(path)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS peer_events_v2 (
                    realm TEXT NOT NULL, actor TEXT NOT NULL, action_id TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE, raw BLOB NOT NULL, received_at REAL NOT NULL,
                    PRIMARY KEY (realm, actor, action_id));
                CREATE TABLE IF NOT EXISTS peer_equivocations_v2 (
                    realm TEXT NOT NULL, actor TEXT NOT NULL, action_id TEXT NOT NULL,
                    first_event_id TEXT NOT NULL, other_event_id TEXT NOT NULL, other_raw BLOB NOT NULL,
                    seen_at REAL NOT NULL, PRIMARY KEY (realm, actor, action_id, other_event_id));
            """)

    def _connect(self):
        return sqlite3.connect(self._path)

    def record(self, verified: Verified, raw: bytes) -> str:
        """'enqueued' the first time, 'replay' for the same event again, 'equivocation' when a
        different event arrives under a recorded identity (the first stays authoritative)."""
        b = verified.body
        key = (b["realm"], b["actor"], b["action_id"])
        with self._lock, self._connect() as db:
            row = db.execute("SELECT event_id FROM peer_events_v2 WHERE realm=? AND actor=? AND action_id=?",
                             key).fetchone()
            if row is None:
                try:
                    db.execute("INSERT INTO peer_events_v2 VALUES (?,?,?,?,?,?)",
                               (*key, verified.event_id, bytes(raw), time.time()))
                    return "enqueued"
                except sqlite3.IntegrityError:
                    return "replay"  # the same event ID under another identity is not new either
            if row[0] == verified.event_id:
                return "replay"
            db.execute("INSERT OR IGNORE INTO peer_equivocations_v2 VALUES (?,?,?,?,?,?,?)",
                       (*key, row[0], verified.event_id, bytes(raw), time.time()))
            return "equivocation"

    def equivocations(self) -> list[tuple]:
        with self._connect() as db:
            return db.execute("SELECT realm, actor, action_id, first_event_id, other_event_id "
                              "FROM peer_equivocations_v2 ORDER BY seen_at").fetchall()


def receive(raw: bytes, trusted_keys, store: PeerEventStore) -> tuple[str, str]:
    """Authenticate and record one wire event: (disposition, event_id). Raises WireError for
    anything that must not be recorded. The disposition is not an authorization."""
    verified = verify_event(raw, trusted_keys)
    return store.record(verified, raw), verified.event_id
