"""Guards for server.py blueprint auto-discovery.

Two things this protects:
  1. The frozen-build fallback manifest (server.ROUTE_MODULES) must not drift
     from the actual routes/ directory — otherwise a route added by a future
     dev would work in `pip install` runs (pkgutil auto-discovers) but silently
     404 in the packaged .exe.
  2. The core + v5 API endpoints must actually register on the app — a
     regression test for the discovery bug where the whole API 404'd.
"""
from __future__ import annotations

import os

from agent_friday import server


def test_route_manifest_matches_routes_dir():
    import agent_friday.routes as routes_pkg
    routes_dir = os.path.dirname(routes_pkg.__file__)
    on_disk = sorted(
        f[:-3] for f in os.listdir(routes_dir)
        if f.endswith(".py") and not f.startswith("_")
    )
    assert sorted(server.ROUTE_MODULES) == on_disk, (
        "server.ROUTE_MODULES (the frozen-.exe fallback) drifted from routes/. "
        "Add/remove the module name to keep the packaged binary's API complete."
    )


def test_core_and_v5_endpoints_registered():
    rules = {str(r.rule) for r in server.app.url_map.iter_rules()}
    for path in (
        "/api/health",
        "/api/soul",
        "/api/user-model",
        "/api/learning/state",
        "/api/memory/dream/state",
        "/api/channels",
        "/api/onboarding/state",
    ):
        assert path in rules, f"{path} not registered — blueprint discovery broken"
