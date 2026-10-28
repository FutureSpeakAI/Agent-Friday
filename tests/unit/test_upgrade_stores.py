"""The stores the upgrade runner, the diagnostic start and the after-upgrade
list keep are in the store inventory (north star §37.3-1) and travel in a
bundle as what they are: the schema with the settings, the upgrade records
with the receipts, and this PC's diagnostic reports not at all."""
from __future__ import annotations

import json
from pathlib import PurePosixPath


def _stores():
    from agent_friday.services import store_inventory as si
    return {s.location: s for s in si.STORES}


def test_the_new_stores_are_in_the_inventory():
    st = _stores()
    for loc in ("schema.json", "migrations/", "diagnostics/"):
        assert loc in st, "the store inventory does not list %s" % loc
    assert st["schema.json"].durable and st["migrations/"].durable
    assert st["diagnostics/"].durable is False


def test_the_schema_number_is_its_version(tmp_path):
    st = _stores()
    assert "schema.json" in st, "the store inventory does not list schema.json"
    from agent_friday.services import store_inventory as si
    home = tmp_path / ".friday"
    home.mkdir()
    (home / "schema.json").write_text(json.dumps({"schema": 1, "stores": {}}), encoding="utf-8")
    rows = {r["store"]: r for r in si.public(si.inventory(home))["stores"]}
    assert str(rows["data_schema"]["version"]) == "1"


def test_a_bundle_carries_the_schema_and_the_records_and_never_the_diagnostics():
    assert "diagnostics/" in _stores(), "the store inventory does not list diagnostics/"
    from agent_friday.services import friday_bundle as fb
    assert fb.category_of(PurePosixPath("schema.json")) == "settings"
    assert fb.category_of(PurePosixPath("migrations/receipt-0-to-1-x.json")) == "receipts"
    assert fb.category_of(PurePosixPath("migrations/unresolved.json")) == "receipts"
    assert fb.category_of(PurePosixPath("diagnostics/NEXT")) is None
    assert fb.category_of(PurePosixPath("diagnostics/diagnostic-x.json")) is None
