"""The old acks route (routes/core_routes /api/onboarding/acks) cannot claim section 9.6.

A regression guard, not red evidence: those two answers are the owner's press on
POST /api/onboarding/answer (routes/first_synthesis), so the acks route must keep ignoring them.
"""
from __future__ import annotations

from tests.desk_press_support import page_headers


def test_the_old_acks_route_cannot_claim_section_9_6(client):
    r = client.post("/api/onboarding/acks", json={"goal_skipped": True, "action_place_ack": True},
                    headers=page_headers())
    assert r.status_code == 200
    acks = r.get_json()["acks"]
    assert acks.get("goal_skipped") is None and acks.get("action_place_ack") is None
