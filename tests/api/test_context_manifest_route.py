"""GET /api/privacy/manifest/<id>: one send's context manifest, for the
person at this PC (BD-15 slice P3). Synthetic fixtures only."""
from __future__ import annotations

import json


def test_a_manifest_opens_by_its_id_and_an_unknown_one_is_404(client, tmp_path, monkeypatch):
    from agent_friday.services import egress_gate as eg
    log = tmp_path / "vault" / "egress-log.jsonl"
    log.parent.mkdir(parents=True)
    monkeypatch.setattr(eg, "_DEFAULT_LOG", log)
    m = {"manifest_version": 1, "manifest_id": "cm-eg-synthetic0001", "call_id": "eg-synthetic0001",
         "sources": [{"part": "messages[].content", "sensitivity": "PUBLIC", "action": "allow", "count": 1}],
         "previewed": False}
    log.with_name("context-manifests.jsonl").write_text(json.dumps(m) + "\n", encoding="utf-8")
    ok = client.get("/api/privacy/manifest/cm-eg-synthetic0001")
    assert ok.status_code == 200
    assert ok.get_json()["manifest"]["sources"][0]["part"] == "messages[].content"
    assert client.get("/api/privacy/manifest/cm-eg-nothere").status_code == 404
    assert client.get("/api/privacy/manifest/..%2f..%2fsecret").status_code in (403, 404)
