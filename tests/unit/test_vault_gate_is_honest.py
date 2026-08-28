"""The vault gate must be ON by default, and must say what it is actually doing.

Two independent failures met at one toggle in Settings -> Privacy.

The SERVER gates on ``settings.model_routing.vault_local_only`` and defaults it
to True when absent, so vault content is held on-device unless someone
deliberately turns that off. The TOGGLE read a bare top-level
``s.vault_local_only`` — a key that does not exist at that level — so it
rendered OFF on every install whose settings had never been hand-edited. A
security control displaying the opposite of its state is worse than no control,
because it invites exactly the wrong conclusion about where private data goes.

Then, having shown the wrong state, it wrote the wrong key: ``save({
vault_local_only })`` persisted a NEW top-level key that nothing on the server
ever reads. So the switch was inert in both directions — the display lied, and
the click did nothing but leave a decoy in settings.json.

Fixing the write introduced a third hazard, which is why _save_settings is
tested here too: only ``capability_routing`` was deep-merged, so a partial
``model_routing`` delta replaced the whole block and silently reset `mode`,
`vault_cloud_fallback` and the rest.
"""

from pathlib import Path

import pytest

import agent_friday.core as core
from agent_friday.core import DEFAULT_SETTINGS

_INDEX = Path(__file__).resolve().parents[2] / "index.html"


def _html() -> str:
    return _INDEX.read_text(encoding="utf-8", errors="ignore")


class TestShippedDefaultIsOn:
    """The flag exists to be turned off locally. It must never ship off."""

    def test_factory_default_is_true(self):
        assert (DEFAULT_SETTINGS["model_routing"]["vault_local_only"] is True)

    def test_absent_key_reads_as_on(self, monkeypatch):
        from agent_friday.services import model_router as mr
        monkeypatch.setattr(mr, "_load_settings",
                            lambda: {"model_routing": {"mode": "cloud_only"}})
        assert mr._vault_local_only() is True

    def test_absent_block_reads_as_on(self, monkeypatch):
        from agent_friday.services import model_router as mr
        monkeypatch.setattr(mr, "_load_settings", lambda: {})
        assert mr._vault_local_only() is True

    def test_unreadable_settings_read_as_on(self, monkeypatch):
        """Fail closed. A gate that opens when it cannot read itself is not a gate."""
        from agent_friday.services import model_router as mr

        def _boom():
            raise RuntimeError("settings unreadable")
        monkeypatch.setattr(mr, "_load_settings", _boom)
        assert mr._vault_local_only() is True

    def test_only_an_explicit_false_turns_it_off(self, monkeypatch):
        from agent_friday.services import model_router as mr
        monkeypatch.setattr(
            mr, "_load_settings",
            lambda: {"model_routing": {"vault_local_only": False}})
        assert mr._vault_local_only() is False


class TestTheToggleIsWiredToTheGate:
    def test_it_no_longer_reads_a_key_that_does_not_exist(self):
        assert "s.vault_local_only" not in _html(), (
            "the Privacy toggle is reading/writing a bare top-level "
            "vault_local_only; the server gates on "
            "model_routing.vault_local_only")

    def test_it_displays_on_when_the_key_is_absent(self):
        """Matching the server: absent means on, so `!== false`, not `!!`."""
        assert "(s.model_routing || {}).vault_local_only !== false" in _html()

    def test_the_save_preserves_the_rest_of_model_routing(self):
        """_save_settings replaces model_routing wholesale, so the UI must spread."""
        assert "...(s.model_routing || {})" in _html()


class TestPartialModelRoutingSaveKeepsItsSiblings:
    def test_saving_one_key_does_not_reset_the_block(self, friday_dir):
        core._save_settings({"model_routing": {
            "mode": "local_preferred",
            "vault_local_only": True,
            "vault_cloud_fallback": "deny",
        }})
        core._save_settings({"model_routing": {"vault_local_only": False}})
        mr = core._load_settings()["model_routing"]
        assert mr["vault_local_only"] is False
        assert mr["mode"] == "local_preferred", (
            "a partial model_routing delta reset the routing mode")
        assert mr["vault_cloud_fallback"] == "deny"
