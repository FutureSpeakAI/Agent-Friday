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


# ── Zero-skip guards ──────────────────────────────────────────────────────
# Added 2026-08-20. The two tests above did NOT catch a real seven-week
# regression: routes/jobs.py failed to import on ~70 consecutive starts while
# 'jobs' stayed in ROUTE_MODULES (so the manifest test passed) and no checked
# path was a pipeline route (so the endpoint test passed). The server logged one
# WARNING and reported itself healthy. See docs/audits/server-death-forensics.md.
#
# These assert the thing that actually matters: every route module that exists
# must actually register. Cheapest durable guard we have.


def test_no_blueprint_was_skipped():
    """Every route module imported cleanly. This is the July-1 catcher."""
    skipped = server.BLUEPRINT_REPORT.get("skipped") or []
    assert skipped == [], (
        "Route module(s) failed to import and were silently skipped: "
        + "; ".join(f"{s['module']}: {s['error']}" for s in skipped)
        + ". A skipped blueprint means an entire API surface is missing while "
          "the server still reports healthy."
    )


def test_every_manifest_module_registered():
    """The registered set covers the full frozen-build manifest."""
    registered = set(server.BLUEPRINT_REPORT.get("registered") or [])
    missing = sorted(set(server.ROUTE_MODULES) - registered)
    assert not missing, f"declared in ROUTE_MODULES but never registered: {missing}"


def test_required_modules_are_never_skipped():
    """REQUIRED modules must load; their absence is fatal, not degraded."""
    skipped = {s["module"] for s in (server.BLUEPRINT_REPORT.get("skipped") or [])}
    fatal = sorted(skipped & set(server.REQUIRED_ROUTE_MODULES))
    assert not fatal, f"required route module(s) failed to load: {fatal}"


def test_career_pipeline_endpoints_registered():
    """Pinned explicitly: these are a live, in-use job search, not a demo.

    routes/jobs.py depends on `data` and `skills`, which live at the REPO ROOT
    rather than inside the installed package - so it is the module most likely
    to silently stop registering again.
    """
    rules = {str(r.rule) for r in server.app.url_map.iter_rules()}
    for path in (
        "/api/pipeline/jobs",
        "/api/pipeline/scan",
        "/api/pipeline/jobs/<job_id>/apply",
        "/api/pipeline/applications/<application_id>/response",
    ):
        assert path in rules, (
            f"{path} not registered - the career pipeline is offline"
        )


def test_startup_report_endpoint_registered():
    """The integrity endpoint must itself be present, or nothing reports."""
    rules = {str(r.rule) for r in server.app.url_map.iter_rules()}
    assert "/api/startup-report" in rules
