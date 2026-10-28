"""Every store declares its encryption, owner scope, migration, backup,
retention and corruption detection (north star §11.7-3), in Friday's own
words; the backup line is read from the bundle's own rules, so the two never
disagree; and the Doctor's store list and every inventory carry them."""
from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]


def _sp():
    assert importlib.util.find_spec("agent_friday.services.store_policy") is not None, \
        "the stores declare nothing about themselves"
    return importlib.import_module("agent_friday.services.store_policy")


def test_every_store_declares_all_six():
    sp = _sp()
    from agent_friday.services import store_inventory as si
    pol = sp.all_policies()
    assert set(pol) == {s.name for s in si.STORES}
    for name, p in pol.items():
        assert set(p) == set(sp.KEYS), name
        assert all(isinstance(v, str) and v.strip() for v in p.values()), (name, p)


def test_the_backup_line_is_what_a_bundle_does():
    sp = _sp()
    from agent_friday.services import friday_bundle as fb, store_inventory as si
    pol = sp.all_policies()
    for s in si.STORES:
        loc = s.location.rstrip("/")
        cat = fb.category_of(PurePosixPath(loc + "/x") if s.is_folder else PurePosixPath(loc))
        line = pol[s.name]["backup"]
        if cat is None:
            assert line.startswith("never"), (s.name, line)
        else:
            assert line.startswith("in a Friday Bundle, as %s" % cat) or line.startswith("always"), (s.name, line)
    assert pol["provider_keys"]["backup"].startswith("never")          # sign-ins never travel
    assert pol["settings"]["backup"] == "in a Friday Bundle, as settings"


def test_credentials_and_the_vault_never_read_as_plain_files():
    sp = _sp()
    from agent_friday.services import store_inventory as si
    pol = sp.all_policies()
    for s in si.STORES:
        if s.version and s.version[0] == "secret" or s.location.startswith(("vault/", "security/")):
            assert pol[s.name]["encryption"] != sp.ENC_PLAIN, s.name
    assert "hash chain" in pol["action_ledger"]["corruption_check"]


def test_machine_state_belongs_to_this_pc_and_is_not_migrated():
    sp = _sp()
    from agent_friday.services import store_inventory as si
    pol = sp.all_policies()
    for s in si.STORES:
        if not s.durable:
            assert pol[s.name]["owner_scope"] == "this PC" and pol[s.name]["migration"].startswith("none")


def test_the_inventory_and_the_doctor_page_carry_the_declarations(tmp_path):
    _sp()
    from agent_friday.services import store_inventory as si
    home = tmp_path / ".friday"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"version": 3}), encoding="utf-8")
    rows = {r["store"]: r for r in si.public(si.inventory(home))["stores"]}
    assert rows["settings"]["policy"]["backup"] == "in a Friday Bundle, as settings"
    html = (ROOT / "static" / "doctor.html").read_text(encoding="utf-8")
    assert "'protection', 'backup'" in html and "pol.encryption, pol.backup" in html
