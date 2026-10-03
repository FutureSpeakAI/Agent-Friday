"""People trust is never shown to another principal.

The observer principal sees `/api/tasks` and `/api/activity` only. Nothing
under `/api/trust`, `/api/people` or `/api/contacts` answers it.

Red by the named mutation: add "/api/trust" (or "/api/people") to
`services/observer_access.READ_ONLY_PREFIXES` and
`test_observer_gets_no_trust_route` fails.
"""
from __future__ import annotations

from agent_friday.services import observer_access as obs


def _mint(client):
    return {obs.HEADER: client.post("/api/tasks/observer-token").get_json()["token"]}


def test_observer_gets_no_trust_route(client):
    hdr = _mint(client)
    for path in ("/api/trust", "/api/people", "/api/contacts",
                 "/api/trust/log?person=pat_example", "/api/people/records/pat"):
        r = client.get(path, headers=hdr)
        assert r.status_code == 403, (path, r.status_code)
    for path in ("/api/trust/edit", "/api/trust/correct", "/api/trust/add-person", "/api/people/forget"):
        r = client.post(path, headers=hdr, json={"person": "x", "event_id": "x", "name": "x"})
        assert r.status_code == 403, (path, r.status_code)


def test_no_trust_prefix_is_on_the_observer_allowlist():
    assert not any(p.startswith(("/api/trust", "/api/people", "/api/contacts"))
                   for p in obs.READ_ONLY_PREFIXES)
