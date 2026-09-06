"""Phase 4's two new routes (docs/design/headroom.md §8.1, §8.2, §8.3,
§12 Phase 4 items 1 and 3): the pre-fetch preflight card and the yield
button's stub.
"""
from __future__ import annotations


def test_fetch_preflight_requires_a_model(client):
    r = client.get("/api/models/fetch/preflight")
    assert r.status_code == 400


def test_fetch_preflight_unknown_model_is_a_404(client):
    r = client.get("/api/models/fetch/preflight?model=nonexistent:99b")
    assert r.status_code == 404
    d = r.get_json()
    assert d["status"] == "error"


def test_fetch_preflight_known_model_returns_the_five_line_card(client):
    r = client.get("/api/models/fetch/preflight?model=z-image-turbo-fp8")
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok"
    assert d["model_id"] == "z-image-turbo-fp8"
    card = d["card"]
    for key in ("what", "where", "what_stands_down", "how_long", "feel"):
        assert key in card and card[key], "preflight card is missing '%s'" % key
    # HR1/HR2 travel with the row too -- the card is not a second surface
    # that gets to invent its own vocabulary.
    row = d["row"]
    for axis in ("fits", "runs_well", "worth_it"):
        assert row["verdicts"][axis]["basis"] in (
            "measured", "derived", "declared", "unknown")


def test_machine_level_is_a_stub_that_says_so(client):
    r = client.post("/api/machine/level", json={"level": "yield"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok"
    assert d["accepted"] is True
    assert d["enforced"] is False
    assert "not" in d["message"].lower()


def test_machine_level_defaults_to_yield_with_no_body(client):
    r = client.post("/api/machine/level")
    assert r.status_code == 200
    d = r.get_json()
    assert d["level_requested"] == "yield"
