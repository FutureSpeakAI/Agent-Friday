"""A fresh profile is not held; a moved one is.

Friday's rules (cLaws) are pinned as an HMAC under the governance key. The
pin exists to notice a CHANGE: the rules text or the key differing from what
this profile pinned before, which is what moving ~/.friday to another PC or
Windows account looks like. A brand-new profile has nothing to compare
against, so its first check establishes the pin and outward actions proceed
to their normal decision (a chat yes or a card), not to a hold.

These run against a scratch home with no keychain: the real key path mints a
file key there, exactly as a fresh install without Credential Manager would.
"""
from __future__ import annotations

import json
import sys

import pytest

from agent_friday.governance import action_gate
from agent_friday.governance import proof_of_integrity as poi


@pytest.fixture
def scratch_home(tmp_path, monkeypatch):
    monkeypatch.setattr(action_gate, "friday_home", lambda: str(tmp_path))
    monkeypatch.setattr(poi, "_GOV_KEY_FILE", tmp_path / "vault" / ".governance-key")
    # No keychain at all: `import keyring` fails, so the key lives in the file.
    monkeypatch.setitem(sys.modules, "keyring", None)
    return tmp_path


def _outward():
    # Background work with no grant: the normal decision is a card, not a deny.
    return action_gate.authorize("create_calendar_event", {"title": "x"},
                                 {"is_background_task": True})


def test_a_fresh_profile_pins_on_first_check_and_is_not_held(scratch_home):
    pin = scratch_home / "governance" / "claws.pin.json"
    assert not pin.exists() and not (scratch_home / "vault" / ".governance-key").exists()

    v = _outward()
    assert v.action != "deny", v.reason
    assert "cLaws" not in (v.reason or "")
    assert pin.exists(), "the first check establishes the pin"
    assert (scratch_home / "vault" / ".governance-key").exists()
    assert action_gate.verify_claws() == (True, "intact")


def test_a_moved_profile_is_held_until_the_owner_repins(scratch_home):
    # Pinned on the old PC under that PC's key.
    gov = scratch_home / "governance"
    gov.mkdir(parents=True, exist_ok=True)
    (gov / "claws.pin.json").write_text(json.dumps({
        "claws_hmac": action_gate.claws_hmac(b"k" * 32), "pinned_at": 1.0}),
        encoding="utf-8")

    ok, why = action_gate.verify_claws()        # this PC mints a different key
    assert not ok and "does not match" in why
    v = _outward()
    assert v.action == "deny" and "cLaws" in v.reason

    action_gate.repin_claws()
    assert action_gate.verify_claws() == (True, "intact")
    assert _outward().action != "deny"
