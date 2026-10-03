"""The routing mode changes only on an explicit owner action.

model_routing.mode decides where every turn goes and what it costs. The owner's
mode had moved from local_preferred to cloud_only with no request to change it
in the server's access log. Whatever wrote it, the rule is now code: only the
owner's own mode controls (the Models tab buttons, the "Switch to Cloud-Only"
button on a local-only refusal, the setup answer) can move the mode. Every other
write that carries a mode keeps the current one and logs the refusal with its
caller; every accepted change is logged old -> new.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from agent_friday import core

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "agent_friday"


def _mode():
    core._invalidate_settings_cache()
    return (core._load_settings_raw().get("model_routing") or {}).get("mode")


@pytest.fixture
def local_preferred(friday_dir):
    try:
        core._save_settings({"model_routing": {"mode": "local_preferred"}},
                            owner_routing_change=True)
    except TypeError:          # a build without the rule: any write moves it
        core._save_settings({"model_routing": {"mode": "local_preferred"}})
    assert _mode() == "local_preferred"
    yield


def test_a_stale_whole_settings_write_cannot_move_the_mode(local_preferred, caplog):
    stale = core._load_settings_raw()
    stale["model_routing"] = dict(stale["model_routing"], mode="cloud_only")
    stale["voice_room_approvals_require_name"] = True
    with caplog.at_level(logging.WARNING, logger="friday.settings"):
        core._save_settings(stale)
    assert _mode() == "local_preferred"
    assert core._load_settings_raw()["voice_room_approvals_require_name"] is True, \
        "the rest of the write must still land"
    assert any("REFUSED a routing mode change" in r.getMessage() for r in caplog.records)


def test_a_partial_block_carrying_a_mode_cannot_move_it(local_preferred):
    core._save_settings({"model_routing": {"mode": "cloud_only", "vault_local_only": False}})
    assert _mode() == "local_preferred"
    core._invalidate_settings_cache()
    assert core._load_settings_raw()["model_routing"]["vault_local_only"] is False


def test_an_explicit_owner_change_is_accepted_and_logged(local_preferred, caplog):
    with caplog.at_level(logging.WARNING, logger="friday.settings"):
        core._save_settings({"model_routing": {"mode": "cloud_only"}},
                            owner_routing_change=True)
    assert _mode() == "cloud_only"
    assert any("routing mode changed local_preferred -> cloud_only" in r.getMessage()
               for r in caplog.records)


def test_only_the_owner_mode_paths_may_pass_the_owner_flag():
    allowed = {"core/__init__.py", "routes/core_routes.py", "services/setup_complete.py"}
    found = set()
    for p in SRC.rglob("*.py"):
        if "owner_routing_change" in p.read_text(encoding="utf-8-sig", errors="ignore"):
            found.add(p.relative_to(SRC).as_posix())
    assert found <= allowed, f"new code passes owner_routing_change: {sorted(found - allowed)}"


@pytest.mark.parametrize("ui", ["index.html", "ui_parts/app.html"])
def test_the_uis_mark_mode_changes_and_never_spread_a_stale_block(ui):
    text = (REPO / ui).read_text(encoding="utf-8")
    compact = re.sub(r"\s+", "", text)
    # Every request body that sets a routing mode says it is the owner's action.
    for m in re.finditer(r"model_routing:\{mode:", compact):
        window = compact[max(0, m.start() - 220):m.start()]
        assert "owner_action:'routing_mode'" in window or "ownerAction:'routing_mode'" in compact[m.start():m.start() + 120], (
            f"{ui}: a routing-mode write without owner_action near "
            f"{compact[max(0, m.start() - 80):m.start() + 60]}")
    # No write spreads the page's copy of model_routing (it carries a stale mode).
    for bad in ("model_routing:{...(s.model_routing", "model_routing:{...cur,mode}",
                "constnext=Object.assign({},mr);"):
        assert bad not in compact, f"{ui} still spreads a stale model_routing block: {bad}"


def test_the_mode_buttons_trust_the_save_response_not_a_second_read():
    """"That mode did not stick" fired after a 200: the separate read-back could
    be served a settings copy cached by a reader that overlapped the save."""
    text = (REPO / "index.html").read_text(encoding="utf-8")
    i = text.index("const setMode = (id) => {")
    body = text[i:i + 1600]
    assert "apiFetch('/api/settings'))" not in body, "setMode still re-reads settings after saving"
    assert "d.settings" in body and "did not stick" not in body
