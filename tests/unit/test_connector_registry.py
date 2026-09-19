"""One catalogue of what Friday can connect to.

Phase 4 of docs/design/connector-ecosystem.md. There were nine lists of what
COULD be connected, and which one you had to edit depended on what kind of
thing you were adding. Nothing anywhere answered "what can Friday connect to",
which is how twelve connectors stayed invisible for months without anyone
being able to notice by reading a list.
"""
from __future__ import annotations

import pytest

from agent_friday.services import connector_registry as R


@pytest.fixture(autouse=True)
def clean():
    R._reset_for_tests()
    yield
    R._reset_for_tests()


def test_all_three_connector_catalogues_are_in_one_list():
    sources = {e.source for e in R.entries()}
    assert "connectors.CONNECTOR_DEFS" in sources
    assert "platforms.ADAPTER_MODULES" in sources, "publishing platforms missing"
    assert "channels.CHANNELS" in sources, "channel bridges missing"


def test_ids_are_unique():
    ids = [e.id for e in R.entries()]
    assert len(ids) == len(set(ids)), "a later catalogue shadowed an earlier one"


def test_the_established_connectors_come_first_and_in_order():
    """The page has an order people are used to. Adding twelve entries must
    not reshuffle the six that were already there."""
    from agent_friday.services.connectors import CONNECTOR_ORDER
    ids = [e.id for e in R.entries()]
    assert ids[:len(CONNECTOR_ORDER)] == list(CONNECTOR_ORDER)


def test_every_entry_is_renderable():
    for e in R.entries():
        d = e.as_dict()
        for k in ("id", "label", "icon", "category", "kind", "auth_mode",
                  "fields", "multi_account", "source"):
            assert k in d, "%s missing from %s" % (k, e.id)
        assert d["category"] in R.CATEGORIES
        assert isinstance(d["fields"], list)


def test_categories_are_mapped_from_the_words_the_catalogues_actually_use():
    """CONNECTOR_DEFS says "Communication", not "messaging". A mapping written
    from imagination dropped Slack and Discord into "other"; this asserts the
    real words are handled."""
    assert R._category_of("Communication") == "messaging"
    assert R._category_of("Productivity") == "productivity"
    assert R._category_of("Development") == "development"
    by_id = {e.id: e for e in R.entries()}
    assert by_id["slack"].category == "messaging"
    assert by_id["github"].category == "development"


def test_an_unknown_category_falls_to_other_rather_than_raising():
    assert R._category_of("interpretive dance") == "other"
    assert R._category_of(None) == "other"


def test_google_is_the_only_multi_account_connector():
    """Stated rather than assumed, so the day a second one gains it, this test
    is the thing that asks whether every caller was updated."""
    multi = {e.id for e in R.entries() if e.multi_account}
    assert multi == {"google"}


def test_a_connector_can_be_declared_with_one_call_whatever_kind_it_is():
    R.register(R.RegistryEntry(id="acme", label="Acme", kind="mcp",
                               category="intelligence", source="test"))
    assert R.get("acme") is not None
    assert R.get("acme").label == "Acme"


def test_registering_something_already_derived_does_not_duplicate_it():
    """First declaration wins, so a runtime registration cannot shadow a real
    mechanism with a stub of the same name."""
    R.register(R.RegistryEntry(id="github", label="Impostor", source="test"))
    rows = [e for e in R.entries() if e.id == "github"]
    assert len(rows) == 1
    assert rows[0].label != "Impostor"


def test_register_refuses_junk():
    for bad in (None, "github", R.RegistryEntry(id="")):
        with pytest.raises(ValueError):
            R.register(bad)


def test_the_catalogue_answers_both_questions_at_once():
    """"What could be connected" and "what is connected" were never askable
    together."""
    c = R.catalogue()
    assert c["total"] == len(R.entries())
    assert c["total"] > 6, "the catalogue is still only the original six"
    assert 0 <= c["connected"] <= c["total"]
    for row in c["connectors"]:
        assert "status" in row and "connected" in row
        assert isinstance(row["connected"], bool)


def test_the_catalogue_survives_a_broken_status_layer(monkeypatch):
    """The list of what COULD be connected must not depend on the thing that
    reports what IS - that coupling is how one failure blanks a whole page."""
    import agent_friday.services.connectors as C
    monkeypatch.setattr(C, "list_connectors",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    c = R.catalogue()
    assert c["total"] == len(R.entries())
    assert c["connected"] == 0
    assert all(r["status"] == "unknown" for r in c["connectors"])


def test_by_category_covers_every_entry():
    grouped = R.by_category()
    assert sum(len(v) for v in grouped.values()) == len(R.entries())
    assert set(grouped) <= set(R.CATEGORIES)


def test_entries_do_not_depend_on_a_working_platforms_package(monkeypatch):
    """One unreadable adapter costs its own entry, never the catalogue."""
    import agent_friday.services.connector_adapters as A
    monkeypatch.setattr(A, "platform_connectors",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    ids = {e.id for e in R.entries()}
    assert "google" in ids and "channel_telegram" in ids
