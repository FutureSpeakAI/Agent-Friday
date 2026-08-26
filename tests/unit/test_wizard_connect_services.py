"""The wizard must not report a connection it did not make.

"CONNECT SERVICES (optional)" is step 9 of the installer's setup wizard.
Stephen, after installing Friday on Janet's laptop 2026-08-26:

    "The connect services portion of the installer, once the gui comes up,
     should be interactive. I could not click to connect my accounts and
     would like to."

He is describing a dead end, and it was worse than inert. Answering "yes"
to "Enable Gmail?" ran:

    connected[cid] = {"enabled": True}
    console.print("(Full Gmail setup runs on first use via the UI)")

No OAuth. No browser. No account. It wrote `enabled: True` into
~/.friday/config.yaml, a key NOTHING in the tree reads — the real Google
accounts live in services/google_accounts.py behind credential_store, and
the real connector registry is services/connectors.py. So the wizard
recorded a connection in a file no code consults, and on the next run
rendered a green ● beside a service that had never been connected.

That is Friday claiming a capability she does not have, which is the one
thing this codebase refuses to do anywhere else — no-receipt-no-render on
the Edition, the capability manifest on /api/health, the tool receipts on
every chat turn. The wizard was the gap.

Genuinely clicking to connect is gated on Friday shipping an OAuth client
(docs/design/google-oauth-onboarding.md). Until that is decided, the step
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
    # The old copy promised setup would "run on first use via the UI" — it did
    # not, and that sentence is what sent Janet looking for a screen that
    # would have asked her for a JSON file.
    assert "on first use" not in said


def test_declining_leaves_prior_state_untouched(monkeypatch):
    monkeypatch.setattr(wiz.Confirm, "ask", staticmethod(lambda *a, **k: False))
    prior = {"gmail": {"enabled": True}}
    out = wiz.step_connectors(10, prior)
    assert out == prior
