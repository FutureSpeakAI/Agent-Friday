"""POST /api/reflex/resolve decides and does not run.

The route returns the resolver's Resolution. The command in it is run by the
caller through the ordinary governed tool path, so the route itself must
never open anything.
"""
from __future__ import annotations

import threading

from agent_friday.services import laya_backend, laya_resolver, laya_runtime
from agent_friday.services.laya_resolver import Candidate


class _Laya:
    def predict(self, text, questions):
        out = {}
        for q in questions:
            if q == "open_request":
                out[q] = {"choice": "open"}
            elif q == "item_kind":
                out[q] = {"choice": "workspace"}
        return {"answers": out}


def test_the_route_decides_and_does_not_run(client, monkeypatch):
    monkeypatch.setattr(laya_backend, "_agent", _Laya())
    monkeypatch.setattr(laya_backend, "_scoring",
                        threading.BoundedSemaphore(laya_backend._MAX_SCORING))
    laya_runtime.clear_cache()
    monkeypatch.setattr(laya_resolver, "CANDIDATE_SOURCES", {
        "workspace": lambda t: [Candidate("workspace", "news", "News", "workspace", 1.0)]})
    ran = []
    from agent_friday.services import desktop_targets
    monkeypatch.setattr(desktop_targets, "open_on_desktop", lambda *a, **k: ran.append(1))
    r = client.post("/api/reflex/resolve", json={"text": "open the news"})
    assert r.status_code == 200, r.data[:200]
    body = r.get_json()
    assert body["status"] == "command"
    assert body["command"] == {"tool": "navigate_to",
                               "input": {"kind": "workspace", "workspace": "news"}}
    assert ran == []
    laya_backend._agent = None
    laya_runtime.clear_cache()


def test_the_route_needs_text(client):
    assert client.post("/api/reflex/resolve", json={}).status_code == 400
