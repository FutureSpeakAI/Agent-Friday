"""GET /api/privacy/map and /api/privacy/map/left: the owner's own view, on
this PC only (BD-15 slice P1). Synthetic fixtures only."""
from __future__ import annotations

import json
import time


def _isolate(tmp_path, monkeypatch):
    import agent_friday.paths as paths
    from agent_friday.services import egress_gate
    monkeypatch.setattr(paths, "friday_home", lambda: tmp_path)
    log = tmp_path / "vault" / "egress-log.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"ts": time.time(), "provider": "anthropic", "field": "messages",
                               "tier": "TIER_0", "action": "allow", "reason": "ok"}) + "\n",
                   encoding="utf-8")
    monkeypatch.setattr(egress_gate, "_DEFAULT_LOG", log)


def test_the_map_is_served_to_the_local_owner(client, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    resp = client.get("/api/privacy/map")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert [d["to"] for d in body["left"]["destinations"]] == ["anthropic"]
    assert body["stores"]


def test_the_receipts_route_needs_a_destination(client, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert client.get("/api/privacy/map/left").status_code == 400
    rows = client.get("/api/privacy/map/left?to=anthropic").get_json()["rows"]
    assert len(rows) == 1 and rows[0]["provider"] == "anthropic"


def test_a_request_from_another_computer_is_refused(client, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert client.get("/api/privacy/map").status_code == 200
    import agent_friday.core as core
    monkeypatch.setattr(core, "_is_local_request", lambda: False)
    assert client.get("/api/privacy/map").status_code == 403


def test_a_cross_site_page_cannot_read_it(client, tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert client.get("/api/privacy/map", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    resp = client.get("/api/privacy/map", headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403
