"""Verify a linked test agreement and milestone allocation. Never performs any effects."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

from check_wire import ROOT, Invalid, canonical, digest, event_id, fail, matches, verify

SCHEMAS = {name: json.loads((ROOT / "schema" / (name + ".schema.json")).read_text())
           for name in ("stage-policy-v1", "economic-schedule-v1")}


def verify_case(fixture):
    offer, acceptance = fixture["offer"], fixture["acceptance"]
    pins = fixture["trusted_peers"]
    cache = {}
    _, offer_id = verify(canonical(offer), set(pins.values()), cache)
    verify(canonical(acceptance), set(pins.values()), cache)
    b, a = offer["body"], acceptance["body"]
    terms = b["payload"]["terms"]
    if (b["type"] != "job.offer" or b["actor"] != pins["requester"]
            or a["type"] != "job.accept" or a["actor"] != pins["provider"]
            or b["recipient"] != a["actor"] or a["recipient"] != b["actor"]
            or a["parents"] != [offer_id] or a["payload"]["offer"] != offer_id
            or any(a[k] != b[k] for k in ("realm","job","stage","terms_hash"))):
        fail("acceptance_binding")

    def resolve(ref):
        if ref not in fixture["attachments_b64"]:
            fail("unresolved_attachment")
        blob = base64.b64decode(fixture["attachments_b64"][ref], validate=True)
        if digest(blob) != ref:
            fail("attachment_hash")
        return blob

    def document(ref, name):
        raw = resolve(ref)
        doc = json.loads(raw)
        if canonical(doc) != raw or not matches(doc, SCHEMAS[name]):
            fail("attachment_schema")
        return doc

    refs = {terms[k] for k in ("acceptance_spec","interruption_spec","review_spec")}
    if len(refs) != 1:
        fail("policy_slots")
    p = document(refs.pop(), "stage-policy-v1")
    for field in ("rights_spec","governance_spec","deliverable_spec"):
        resolve(terms[field])
    for ref in p["evidence"] + terms["scope"]["approved_tools"]:
        resolve(ref)
    if not p["delivery_deadline"] < p["review_deadline"] < p["appeal_deadline"]:
        fail("policy_deadlines")
    schedule = document(p["economic_schedule_hash"], "economic-schedule-v1")
    if any(schedule[k] != terms[k] for k in ("job","stage","revision","issuer")):
        fail("economic_identity")
    awards = schedule["awards"]
    final = awards[-1]
    if (final["gross_units"] != terms["gross_units"] or final["shares"] != terms["shares"]):
        fail("economic_total")
    if p["stop_awards"] != [{"milestone":row["milestone"],"cumulative_units":row["gross_units"]} for row in awards]:
        fail("stop_awards")
    milestones = {}
    previous = {}
    gross_before = -1
    for row in awards:
        names = [(s["beneficiary"],s["role"]) for s in row["shares"]]
        amounts = dict(zip(names, (s["units"] for s in row["shares"])))
        if (row["milestone"] in milestones or row["gross_units"] <= gross_before or names != sorted(set(names))
                or sum(amounts.values()) != row["gross_units"] or (previous and previous.keys() != amounts.keys())
                or any(amounts[k] < previous.get(k, 0) for k in amounts)):
            fail("award_rows")
        fee = sum(n for (_,role), n in amounts.items() if role == "operator")
        if fee > row["gross_units"] * schedule["operator_bps"] // 10000:
            fail("operator_cap")
        previous, gross_before = amounts, row["gross_units"]
        milestones[row["milestone"]] = row
    request = fixture["award_to_check"]
    agreed = milestones.get(request["milestone"])
    if agreed != request:
        fail("unagreed_award")
    return "compatible_agreement"


def main():
    base = json.loads((ROOT / "fixtures/linked-stage.json").read_text())
    assert verify_case(base) == "compatible_agreement"
    for case in base["negative_cases"]:
        v = copy.deepcopy(base)
        for edit in case["edits"]:
            item = v
            for segment in edit["path"][:-1]:
                item = item[segment]
            end = edit["path"][-1]
            if edit["op"] == "remove":
                del item[end]
            else:
                item[end] = edit["value"]
        try:
            verify_case(v)
        except Invalid as exc:
            assert str(exc) == case["expect"], (case["name"], str(exc))
        else:
            raise AssertionError(case["name"] + " unexpectedly accepted")
    print(f"1 complete offer-to-economy agreement and {len(base['negative_cases'])} rejection cases passed; no balance or work was advanced.")


if __name__ == "__main__":
    main()
