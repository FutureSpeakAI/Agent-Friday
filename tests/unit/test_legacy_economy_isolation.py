"""The legacy milliPositron economy and the issuer journal never meet (NS-40.4-16, NS-40.4-14).

Legacy services/economy.py mints and earns freely. The issuer journal is a fixed-supply ledger
(NS-40.4-2). Nothing legacy may become a journal balance. Held here by two checks: neither module
imports the other, and a real run of every legacy credit leaves a journal's balances unchanged."""
import ast
from pathlib import Path

import pytest

from agent_friday.services import economy as legacy
from agent_friday.services import issuer_journal as journal

SERVICES = Path(journal.__file__).parent
ISSUER, SUPPLY = "issuer-test-1", 1_000_000


def _imports(path: Path) -> set:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found.update(f"{node.module}.{a.name}" for a in node.names)
    return found


def _mentions(imports: set, module: str) -> bool:
    return any(i == module or i.endswith("." + module) or i.endswith(module) for i in imports)


def test_neither_module_imports_the_other():
    assert not _mentions(_imports(SERVICES / "economy.py"), "issuer_journal")
    assert not _mentions(_imports(SERVICES / "issuer_journal.py"), "economy")


def test_the_import_check_can_fail(tmp_path):
    bad = tmp_path / "m.py"
    bad.write_text("from agent_friday.services import issuer_journal\n", encoding="utf-8")
    assert _mentions(_imports(bad), "issuer_journal")
    bad.write_text("import agent_friday.services.economy as e\n", encoding="utf-8")
    assert _mentions(_imports(bad), "economy")


def test_every_legacy_credit_leaves_a_journal_unchanged(tmp_path):
    legacy._ensure_schema()
    j = journal.IssuerJournal(tmp_path / "j.sqlite", ISSUER, SUPPLY)
    before = j.balances()
    results = [legacy.earn("iso-a", 5000, "test"), legacy.mint_negatron("iso-a", 700, "test"),
               legacy.apply_genesis_bonus("iso-b"), legacy.transfer("iso-a", "iso-b", 100, "test")]
    assert all(r is not None for r in results), "a legacy call failed, so this proved nothing"
    assert j.balances() == before == {"treasury": SUPPLY}


def test_the_behaviour_check_sees_a_change_when_there_is_one(tmp_path):
    j = journal.IssuerJournal(tmp_path / "j.sqlite", ISSUER, SUPPLY)
    before = j.balances()
    outcome = j.execute({"issuer": ISSUER, "effect": "e1", "action": "a1",
                         "postings": {"treasury": -5, "payee": 5}})
    assert outcome == "posted" and j.balances() != before
