"""The exchange package's boundaries are checks, not conventions (NS-40.4-11 to -13, design D.1, D.4, EC-6)."""
import ast
import dataclasses
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src" / "agent_friday" / "services" / "exchange"
spec = importlib.util.spec_from_file_location("check_exchange_imports", ROOT / "scripts" / "check_exchange_imports.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_nothing_under_the_exchange_package_imports_a_network_module():
    assert guard.run() == []


def test_the_import_guard_can_fail(tmp_path):
    (tmp_path / "ok.py").write_text("import json\nfrom dataclasses import dataclass\n", encoding="utf-8")
    assert guard.run(tmp_path) == []
    for line in ("import socket", "import requests", "from urllib import request", "import urllib.request",
                 "from http.client import HTTPConnection", "import asyncio", "import stripe"):
        (tmp_path / "bad.py").write_text(line + "\n", encoding="utf-8")
        assert guard.run(tmp_path), line


BANNED_FIELD = re.compile(r"(card|bank|routing|iban|account_?number|cvv|cvc|holder|\bpan\b|ssn|passport)", re.I)


def _classes():
    from agent_friday.services.exchange import fees, orders, provider
    for module in (orders, provider, fees):
        for _, cls in vars(module).items():
            if dataclasses.is_dataclass(cls) and cls.__module__ == module.__name__:
                yield cls


def test_no_exchange_type_carries_a_card_bank_or_account_holder_field():
    seen = list(_classes())
    assert len(seen) >= 10
    for cls in seen:
        for field in dataclasses.fields(cls):
            assert not BANNED_FIELD.search(field.name), f"{cls.__name__}.{field.name}"


def test_the_field_guard_can_fail():
    for name in ("card_number", "bank_account", "routing_number", "account_holder", "IBAN", "cvv"):
        assert BANNED_FIELD.search(name), name
    for name in ("payer", "provider_ref", "cash_recipient", "reservation_ref"):
        assert not BANNED_FIELD.search(name), name


def test_only_provider_code_builds_buying_power():
    allowed = {"provider.py", "fake_provider.py"}
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "BuyingPower":
                assert path.name in allowed and path.parent.name == "exchange", f"{path} builds BuyingPower"


# ── second-review notes (PR #903), written red first ──────────────────────────────────────────────

def test_an_order_is_built_only_by_place_buy_and_place_sell():
    """D-8: Order( may be called in src only inside orders.place_buy and orders.place_sell."""
    allowed = {("orders.py", "place_buy"), ("orders.py", "place_sell")}
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for call in [c for c in ast.walk(fn) if isinstance(c, ast.Call)]:
                if getattr(call.func, "id", getattr(call.func, "attr", "")) == "Order":
                    assert (path.name, fn.name) in allowed, f"{path}:{fn.name} builds an Order directly"


def test_the_import_guard_also_catches_dynamic_imports_and_process_and_foreign_function_modules(tmp_path):
    for line in ("import importlib", "from importlib import import_module", "import subprocess", "import ctypes",
                 "x = __import__('socket')", "import importlib.util", "from subprocess import run"):
        (tmp_path / "bad.py").write_text(line + "\n", encoding="utf-8")
        assert guard.run(tmp_path), line
    (tmp_path / "bad.py").write_text("name = 'subprocess'  # only a word\nimport json\n", encoding="utf-8")
    assert guard.run(tmp_path) == []


def test_the_guard_states_its_limit_and_the_providers_own_word_is_called_the_other_half():
    assert "limit" in guard.__doc__ and "test_only" in provider_doc()


def provider_doc():
    from agent_friday.services.exchange import provider
    return provider.__doc__
