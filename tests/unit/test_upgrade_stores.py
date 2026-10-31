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


def test_the_schema_travels_whatever_categories_are_chosen(tmp_path):
    from agent_friday.services import friday_bundle as fb
    assert getattr(fb, "SCHEMA_FILE", None) == "schema.json", "the schema is left out with the settings"
    home = tmp_path / ".friday"
    (home / "ledger").mkdir(parents=True)
    (home / "ledger" / "actions.jsonl").write_text("{}\n", encoding="utf-8")
    (home / "schema.json").write_text(json.dumps({"schema": 1}), encoding="utf-8")
    (home / "settings.json").write_text("{}", encoding="utf-8")
    files = {str(rel) for rel, _ in fb.plan(home, ["receipts"])["files"]}
    assert "schema.json" in files and "settings.json" not in files


def test_the_first_migration_does_not_record_the_schema_file_as_a_store(tmp_path, monkeypatch):
    assert "schema.json" in _stores(), "the store inventory does not list schema.json"
    home = tmp_path / ".friday"
    home.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    from agent_friday.services import migrations as mg
    assert mg.run_pending(home)["ok"]
    assert "data_schema" not in json.loads((home / "schema.json").read_text(encoding="utf-8"))["stores"]


def test_upgrade_items_carried_from_the_other_pc_are_sorted_after_a_restore(tmp_path, monkeypatch):
    assert "migrations/" in _stores(), "the store inventory does not list migrations/"
    home = tmp_path / ".friday"
    home.mkdir()
    monkeypatch.setenv("FRIDAY_HOME", str(home))
    for k in ("FRIDAY_SAFE_START", "FRIDAY_DIAGNOSTIC_START"):
        monkeypatch.setenv(k, "0")
    from agent_friday.services import derived_rebuild as drb, safe_mode, upgrade_review as ur
    monkeypatch.setattr(safe_mode, "_failed_starts", lambda: 0)
    monkeypatch.setattr(drb, "INDEXES", {k: (v[0], lambda: {"ok": True}) for k, v in drb.INDEXES.items()})
    old = ur.add("migration_failed", "migration-2", "An upgrade step failed there.", home=home)
    keep = ur.add("rebuild_failed", "library", "Could not rebuild the Library.", home=home)
    m = home / "restore" / "needs_reindex.json"
    m.parent.mkdir(parents=True)
    m.write_text(json.dumps({"at": old["last_at"] + 1, "indexes": ["library"]}), encoding="utf-8")
    safe_mode.reset_for_tests()
    try:
        drb.run_pending(home)
    finally:
        safe_mode.reset_for_tests()
    left = {i["id"] for i in ur.listing(home)}
    assert old["id"] not in left
    assert keep["id"] not in left                  # the rebuild that now worked sorted its own item
