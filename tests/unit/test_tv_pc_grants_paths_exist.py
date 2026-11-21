"""Every desktop path the phone's standing-grants calls use is a real route on the desktop (threat assessment fix #4).

tests/unit/test_tv_pc_grants.py replaces the desktop call with a stand-in, so it cannot notice that a path the gateway
calls does not exist on the desktop. The three paths are the literals in tv_gateway/pc_grants.py; the desktop app's own
URL map must serve each, with that method. (tests/unit/test_tv_pc_grants_real_routes.py goes through them end to end.)
"""
from __future__ import annotations

import pytest

from agent_friday import core, server  # noqa: F401 (importing server registers every blueprint on core.app)

GATEWAY_PATHS = (("GET", "/api/governance/standing-grants"),
                 ("POST", "/api/governance/standing-grants/stop-offering"),
                 ("DELETE", "/api/governance/grants/<grant_id>"))


@pytest.mark.parametrize("method,path", GATEWAY_PATHS)
def test_every_desktop_path_the_gateway_calls_is_a_real_route(method, path):
    """The paths are the literals pc_grants sends; each must be in the desktop app's own URL map."""
    rules = {(m, r.rule) for r in core.app.url_map.iter_rules() for m in r.methods}
    assert (method, path) in rules, "the desktop serves no %s %s, so the phone's call to it would 404" % (method, path)


def test_the_list_above_is_the_paths_pc_grants_really_sends():
    """So the list cannot drift from the gateway: each literal is in the module's source."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src" / "agent_friday" / "tv_gateway" / "pc_grants.py").read_text("utf-8")
    for _method, path in GATEWAY_PATHS:
        assert path.split("<")[0] in src, path
