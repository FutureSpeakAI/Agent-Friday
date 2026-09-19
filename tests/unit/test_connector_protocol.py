"""Connectors read through one protocol.

Phase 3 of docs/design/connector-ecosystem.md. Publishing platforms and channel
bridges had never appeared on /api/connectors, in connectors_health, in
connected_keys or in the connector-down monitor, because services/connectors.py
imports neither package. A LinkedIn connection that died was a connection
nothing watched.

What these hold: the new rows carry the SAME shape the page has always
consumed, a broken adapter costs its own row and not the page, and the legacy
status word is mapped from the derived verdict rather than computed a second
way - so the two can never disagree.
"""
from __future__ import annotations

import pytest

from agent_friday.services import connector_adapters as A
from agent_friday.services import connector_health as ch
from agent_friday.services import connector_protocol as P


class _Stub(P.Connector):
    id = "stub"
    label = "Stub"
    kind = "test"

    def __init__(self, health):
        self._h = health

    def health(self, account_id=None):
        return self._h


# ── the contract the page consumes ──────────────────────────────────────────

REQUIRED = ("key", "name", "icon", "category", "kind", "blurb", "capabilities",
            "workspaces", "setup_hint", "docs_url", "fields", "status",
            "detail", "connected", "tool_count", "tools")


def test_a_protocol_row_carries_every_field_the_page_expects():
    row = _Stub(ch.Health(state=ch.WORKING)).to_status_dict()
    for k in REQUIRED:
        assert k in row, "%s missing - the page renders this" % k


def test_the_legacy_word_is_derived_not_computed_twice():
    """`connected` and `status` both come from the one verdict, so a row
    cannot say "connected" and be unhealthy at the same time - which is the
    entire family of bugs behind this work."""
    for state in ch.STATES:
        row = _Stub(ch.Health(state=state)).to_status_dict()
        assert row["connected"] is (row["status"] in ("connected", "degraded"))
        assert row["connected"] is ch.Health(state=state).healthy


def test_degraded_gets_its_own_word_rather_than_being_rounded_off():
    """Rounding DEGRADED to "connected" hides the Drive case; rounding it to
    "error" says an account is broken when it works."""
    row = _Stub(ch.Health(state=ch.DEGRADED)).to_status_dict()
    assert row["status"] == "degraded"
    assert row["connected"] is True


def test_a_local_key_fault_and_a_revoked_grant_both_read_as_error_but_differ():
    """The legacy vocabulary cannot tell them apart - it has one word. The
    derived verdict rides alongside precisely so a caller that cares can."""
    a = _Stub(ch.Health(state=ch.NEEDS_USER)).to_status_dict()
    b = _Stub(ch.Health(state=ch.UNREADABLE)).to_status_dict()
    assert a["status"] == b["status"] == "error"
    assert a["health"]["state"] != b["health"]["state"]
    assert a["health"]["action"] != b["health"]["action"]


# ── an adapter must not be able to take down the page ───────────────────────

def test_an_adapter_that_raises_reports_unknown_rather_than_exploding():
    class Boom(P.Connector):
        id = "boom"

        def health(self, account_id=None):
            raise RuntimeError("the vendor SDK exploded")

    row = Boom().to_status_dict()
    assert row["status"] == "unknown" and row["connected"] is False
    assert "exploded" in row["detail"]


def test_an_adapter_returning_junk_is_not_believed():
    class Junk(P.Connector):
        id = "junk"

        def health(self, account_id=None):
            return "fine, honestly"

    assert Junk().to_status_dict()["connected"] is False


def test_accounts_failing_does_not_cost_the_row():
    class BadAccounts(_Stub):
        def accounts(self):
            raise RuntimeError("nope")

    row = BadAccounts(ch.Health(state=ch.WORKING)).to_status_dict()
    assert row["connected"] is True and row["accounts"] == []


# ── the mechanisms that were invisible ──────────────────────────────────────

def test_platforms_and_channels_now_appear():
    rows = {c.id for c in A.extra_connectors()}
    assert "linkedin" in rows, "publishing platforms are still invisible"
    assert "channel_telegram" in rows, "channel bridges are still invisible"


def test_test_only_platforms_stay_hidden():
    """Matched on the canonical id, not the module name - `federation_pub` is
    the module and `federation` is the id, and naming the module hid nothing."""
    rows = {c.id for c in A.extra_connectors()}
    assert "mock" not in rows
    assert "federation" not in rows


def test_every_extra_connector_produces_a_renderable_row():
    for conn in A.extra_connectors():
        row = conn.to_status_dict()
        for k in REQUIRED:
            assert k in row, "%s missing from %s" % (k, conn.id)
        assert isinstance(row["connected"], bool)
        assert row["health"]["state"] in ch.STATES


def test_the_new_rows_do_not_collide_with_the_existing_ones():
    """A channel called discord and an MCP server called discord are different
    things; the keys must not clash or one would silently replace the other."""
    from agent_friday.services import connectors as C
    keys = [r["key"] for r in C.list_connectors()]
    assert len(keys) == len(set(keys)), "duplicate connector keys"


def test_listing_survives_a_broken_adapter(monkeypatch):
    """One bad adapter costs its own row, never the page."""
    from agent_friday.services import connectors as C

    class Exploding(P.Connector):
        id = "exploding"

        def to_status_dict(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(A, "extra_connectors",
                        lambda: [Exploding(), A.ChannelConnector("telegram")])
    keys = [r["key"] for r in C.list_connectors()]
    assert "exploding" not in keys
    assert "channel_telegram" in keys
    assert "google" in keys, "the existing connectors were lost"


def test_the_existing_connectors_are_untouched():
    """Phase 3 adds rows. It must not change the ones that were already there."""
    from agent_friday.services import connectors as C
    rows = {r["key"]: r for r in C.list_connectors()}
    for key in C.CONNECTOR_ORDER:
        assert key in rows, "%s disappeared" % key
        assert rows[key]["kind"] in ("oauth", "mcp")
