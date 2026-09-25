"""The wizard must not report a connection it did not make.

"CONNECT SERVICES (optional)" is step 9 of the installer's setup wizard.
A user who cannot click to connect an account there hits a dead end, and a
step that fakes the connection is worse than inert. The failure shape is
answering "yes" to "Enable Gmail?" and running:

    connected[cid] = {"enabled": True}
    console.print("(Full Gmail setup runs on first use via the UI)")

No OAuth. No browser. No account. That writes `enabled: True` into
~/.friday/config.yaml, a key NOTHING in the tree reads — the real Google
accounts live in services/google_accounts.py behind credential_store, and
the real connector registry is services/connectors.py. So the wizard
records a connection in a file no code consults, and on the next run
renders a green ● beside a service that has never been connected.

That is Friday claiming a capability she does not have, which is the one
thing this codebase refuses to do anywhere else — no-receipt-no-render on
the capability manifest on /api/health, the tool receipts on
every chat turn.

Genuinely clicking to connect is gated on Friday shipping an OAuth client
(docs/design/active/google-oauth-onboarding.md). Until that is decided, the step
tells the truth: what is connected, what is not, and where connecting
actually happens.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_friday import setup_wizard as wiz  # noqa: E402


@pytest.fixture
def always_yes(monkeypatch):
    """The most enthusiastic possible user: says yes to everything."""
    monkeypatch.setattr(wiz.Confirm, "ask", staticmethod(lambda *a, **k: True))


def test_saying_yes_does_not_fabricate_a_connection(always_yes):
    """The wizard cannot perform OAuth, so it must not record that it did."""
    out = wiz.step_connectors(10, {})
    for cid, entry in (out or {}).items():
        assert not (entry or {}).get("enabled"), (
            "%s was marked connected by a step that never contacted Google — "
            "the next run renders a green dot for an account that does not "
            "exist" % cid
        )


def test_a_real_existing_connection_is_still_reported(always_yes, monkeypatch):
    """Honesty runs both ways: a connection that DOES exist must show."""
    monkeypatch.setattr(wiz, "_connected_google_accounts", lambda: ["jan@example.com"])
    out = wiz.step_connectors(10, {})
    assert out.get("gmail", {}).get("enabled") is True
    assert out.get("calendar", {}).get("enabled") is True


def test_the_step_says_where_connecting_actually_happens(always_yes, capsys):
    wiz.step_connectors(10, {})
    said = capsys.readouterr().out.lower()
    assert "settings" in said, "the step must name where the connect flow lives"
    # Promising setup will "run on first use via the UI" is false — nothing
    # runs — and it sends the user looking for a screen that would ask them
    # for a JSON file.
    assert "on first use" not in said


def test_declining_leaves_prior_state_untouched(monkeypatch):
    monkeypatch.setattr(wiz.Confirm, "ask", staticmethod(lambda *a, **k: False))
    prior = {"gmail": {"enabled": True}}
    out = wiz.step_connectors(10, prior)
    assert out == prior
