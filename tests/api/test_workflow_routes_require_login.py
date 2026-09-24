"""Every mutating route in routes/workflows.py carries its own @login_required.

The global `core.check_auth` before_request hook already refuses a
non-loopback caller with no remote key, so these routes are not reachable
through the normal request path without it. The per-route decorator is the
defense in depth that keeps them closed if that hook is ever narrowed (an
exemption list, a second app instance, a refactor). Workflow chains are the
machinery the unattended self-improvement loop runs through, so creating,
deleting or running one must never depend on a single layer.

Reads stay open, matching the split in goals.py and scheduler.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

from agent_friday.routes import workflows as wf

_REMOTE = {"REMOTE_ADDR": "203.0.113.5"}   # TEST-NET-3, never loopback
_DENIED = (401, 403)
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _mutating_routes_without_login():
    tree = ast.parse(Path(wf.__file__).read_text(encoding="utf-8-sig"))
    missing, seen = [], 0
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        methods, routed, guarded = set(), False, False
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "route"):
                routed = True
                for kw in dec.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods |= {e.value.upper() for e in kw.value.elts
                                    if isinstance(e, ast.Constant)}
            name = dec.id if isinstance(dec, ast.Name) else getattr(dec, "attr", None)
            if name == "login_required":
                guarded = True
        if routed and methods & _MUTATING:
            seen += 1
            if not guarded:
                missing.append(node.name)
    return missing, seen


def test_every_mutating_workflow_route_declares_login_required():
    missing, seen = _mutating_routes_without_login()
    assert seen >= 3, "the route scan found no mutating routes; the scan is broken"
    assert missing == [], f"mutating routes with no @login_required: {missing}"


def _status(resp):
    return resp[1] if isinstance(resp, tuple) else resp.status_code


def test_the_chain_routes_refuse_a_remote_caller_on_their_own(app):
    """Called inside a bare request context, so `before_request` never runs:
    only the route's own decorator stands between the caller and the view."""
    cases = [
        ("/api/workflows/chains", "POST", wf.workflow_chains_create, (),
         {"name": "x", "steps": [{"prompt": "y"}]}),
        ("/api/workflows/chains/nonexistent", "DELETE", wf.workflow_chain_delete,
         ("nonexistent",), None),
        ("/api/workflows/chains/nonexistent/run", "POST", wf.workflow_chain_run,
         ("nonexistent",), None),
    ]
    for path, method, view, args, body in cases:
        with app.test_request_context(path, method=method, json=body,
                                      environ_overrides=_REMOTE):
            assert _status(view(*args)) in _DENIED, f"{method} {path} ran for a remote caller"


def test_the_global_hook_also_refuses_a_remote_caller(client):
    resp = client.post("/api/workflows/chains", json={"name": "x", "steps": []},
                       environ_overrides=_REMOTE)
    assert resp.status_code in _DENIED
