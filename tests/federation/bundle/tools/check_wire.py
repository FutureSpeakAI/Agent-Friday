"""Offline conformance for the signed Asimov peer profile. Python 3.10+, cryptography.

This program verifies public fixtures. It never authorizes execution or funds.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[1]
PREFIX = b"asimov-peer/v2/event\x00"
TERMS_PREFIX = b"asimov-terms/v2/terms\x00"
SCHEMA = json.loads((ROOT / "schema/peer-event-v2.schema.json").read_text())


class Invalid(ValueError):
    pass


def fail(reason):
    raise Invalid(reason)


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            fail("duplicate_key")
        result[key] = value
    return result


def checked_tree(item, depth=0):
    if depth > 24:
        fail("depth")
    if type(item) is str:
        if any(not 32 <= ord(c) <= 126 for c in item):
            fail("string")
    elif type(item) is int:
        if not 0 <= item <= 2**53 - 1:
            fail("integer")
    elif type(item) is list:
        for v in item:
            checked_tree(v, depth + 1)
    elif type(item) is dict:
        for k, v in item.items():
            checked_tree(k, depth + 1)
            checked_tree(v, depth + 1)
    elif item is not None and type(item) is not bool:
        fail("type")


def canonical(item):
    checked_tree(item)
    return json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def decode(raw):
    if len(raw) > 65536:
        fail("size")
    try:
        parsed = json.loads(raw.decode("ascii"), object_pairs_hook=pairs,
                            parse_float=lambda _: fail("number"), parse_constant=lambda _: fail("number"))
        if canonical(parsed) != raw:
            fail("noncanonical")
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        fail("encoding")
    return parsed


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def event_id(body):
    return digest(PREFIX + canonical(body))


def terms_hash(terms):
    return digest(TERMS_PREFIX + canonical(terms))


def matches(value, rule):
    """Evaluate only the closed subset of JSON Schema used by the supplied schemas."""
    supported = {"$schema","title","type","enum","const","oneOf","allOf","if","then","properties",
                 "required","additionalProperties","items","maxItems","minItems","uniqueItems","minimum",
                 "maximum","minLength","maxLength","pattern"}
    if set(rule) - supported:
        raise RuntimeError("Unsupported schema keyword")
    if "oneOf" in rule and sum(matches(value, r) for r in rule["oneOf"]) != 1:
        return False
    if "allOf" in rule and not all(matches(value, r) for r in rule["allOf"]):
        return False
    if "if" in rule and matches(value, rule["if"]) and not matches(value, rule["then"]):
        return False
    if "const" in rule and (type(value) is not type(rule["const"]) or value != rule["const"]):
        return False
    if "enum" in rule and not any(type(value) is type(x) and value == x for x in rule["enum"]):
        return False
    types = {"null":type(None), "boolean":bool, "object":dict, "array":list, "integer":int, "string":str}
    if "type" in rule and type(value) is not types[rule["type"]]:
        return False
    if isinstance(value, dict):
        fields = rule.get("properties", {})
        if not set(rule.get("required", ())) <= set(value):
            return False
        if rule.get("additionalProperties") is False and set(value) - fields.keys():
            return False
        return all(matches(value[k], fields[k]) for k in fields.keys() & value)
    if isinstance(value, list):
        if not rule.get("minItems", 0) <= len(value) <= rule.get("maxItems", len(value)):
            return False
        if rule.get("uniqueItems") and len({canonical(v) for v in value}) != len(value):
            return False
        return all(matches(v, rule.get("items", {})) for v in value)
    if isinstance(value, str):
        return (rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", len(value))
                and ("pattern" not in rule or re.fullmatch(rule["pattern"], value) is not None))
    if type(value) is int:
        return rule.get("minimum", value) <= value <= rule.get("maximum", value)
    return True


def verify(raw, trust, seen):
    env = decode(raw)
    if not matches(env, SCHEMA):
        fail("schema")
    b = env["body"]
    if b["actor"] not in trust:
        fail("untrusted_key")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(b["actor"])).verify(
            bytes.fromhex(env["signature"]), PREFIX + canonical(b))
    except InvalidSignature:
        fail("signature")
    if b["issued_at"] >= b["expires_at"] or b["parents"] != sorted(b["parents"]):
        fail("header")
    if b["type"] == "job.offer":
        terms = b["payload"]["terms"]
        if terms_hash(terms) != b["terms_hash"] or any(terms[k] != b[k] for k in ("realm","job","stage")):
            fail("terms_mismatch")
        roles = terms["participants"]
        if roles["requester"] != b["actor"] or roles["provider"] != b["recipient"] or roles["provider"] == b["actor"]:
            fail("role")
        if sum(s["units"] for s in terms["shares"]) != terms["gross_units"]:
            fail("split")
    identity = (b["realm"], b["actor"], b["action_id"])
    eid = event_id(b)
    if identity in seen:
        if seen[identity] != eid:
            fail("equivocation")
        return "replay", eid
    seen[identity] = eid
    return "verified_bytes", eid


def main():
    vectors = json.loads((ROOT / "fixtures/wire-v2.json").read_text())
    trust = set(vectors["peer_keys"].values())
    policy = vectors["stage_policy"]
    schema = json.loads((ROOT / "schema/stage-policy-v1.schema.json").read_text())
    assert matches(policy, schema)
    terms = vectors["cases"][0]["envelope"]["body"]["payload"]["terms"]
    for slot in ("acceptance_spec", "review_spec", "interruption_spec"):
        assert digest(canonical(policy)) == terms[slot]
    for field in ("economic_schedule_hash", "evidence", "timeout_action"):
        assert not matches({k: v for k, v in policy.items() if k != field}, schema)
    seen = {}
    count = 0
    for case in vectors["cases"]:
        raw = bytes.fromhex(case["raw_hex"]) if "raw_hex" in case else canonical(case["envelope"])
        try:
            outcome, eid = verify(raw, trust, seen)
            if case.get("event_id") and eid != case["event_id"]:
                raise AssertionError(case["name"] + ": wrong digest")
        except Invalid as error:
            outcome = str(error)
        if outcome != case["expect"]:
            raise AssertionError(f'{case["name"]}: expected {case["expect"]}, got {outcome}')
        count += 1
    print(f"{count} wire cases and 4 policy checks passed; authentication only. Authorization, evidence and domain finality are separate.")

if __name__ == "__main__":
    main()
