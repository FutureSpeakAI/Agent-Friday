"""NS-49.1-5: resource readings and money modules import nothing of each other, checked by a CI script.

The rule (design 10.x, resource vector is never money): a module that measures GPU/CPU time must not import the
journal, the issuer registry, escrow or the exchange, and none of those may import a resource module. #892 held it
with an AST test inside test_resource_vector.py; this file holds the script that CI runs on every push, and the
tests below show the script can fail (Q2). The static limit, stated plainly: a module loaded by a string at run time is
not seen; scripts/check_exchange_imports.py already forbids the dynamic-import helpers inside the exchange package.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_resource_money_imports.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_resource_money_imports", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tree(tmp_path, files):
    services = tmp_path / "services"
    for name, source in files.items():
        path = services / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return services


def test_the_real_tree_is_clean():
    assert _load().run() == []


def test_the_script_exits_zero_on_the_real_tree():
    done = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_resource_module_importing_money_is_found_in_every_import_form(tmp_path):
    forms = {
        "absolute": "import agent_friday.services.issuer_journal\n",
        "from_package": "from agent_friday.services import issuer_registry\n",
        "from_module": "from agent_friday.services.exchange.settlement import Settlement\n",
        "relative": "from . import stage_escrow\n",
        "relative_module": "from .exchange import orders\n",
        "aliased": "import agent_friday.services.issuer_journal as ij\n",
    }
    for label, source in forms.items():
        services = _tree(tmp_path / label, {"resource_vector.py": source})
        found = _load().run(services)
        assert found and "resource_vector.py" in found[0], label


def test_a_money_module_importing_a_resource_module_is_found_including_inside_the_exchange_package(tmp_path):
    services = _tree(tmp_path, {
        "issuer_journal.py": "from agent_friday.services import resource_vector\n",
        "exchange/orders.py": "import agent_friday.services.resource_measure as rm\n",
        "exchange/__init__.py": "",
    })
    found = _load().run(services)
    assert any(f.startswith("issuer_journal.py") for f in found), found
    assert any(f.startswith("exchange/orders.py") or f.startswith("exchange\\orders.py") for f in found), found


def test_a_function_level_import_is_found_too(tmp_path):
    services = _tree(tmp_path, {"resource_measure.py": "def f():\n    from agent_friday.services import economy\n"})
    assert _load().run(services)


def test_unrelated_modules_and_lookalike_names_are_not_flagged(tmp_path):
    services = _tree(tmp_path, {
        "resource_vector.py": "import dataclasses\nfrom agent_friday.services import peer_wire_v2\n",
        "other.py": "from agent_friday.services import resource_vector\nfrom agent_friday.services import issuer_journal\n",
        "exchange_rates_display.py": "from agent_friday.services import resource_vector\n",
    })
    assert _load().run(services) == []


def test_the_exit_code_is_one_with_a_line_per_finding(tmp_path):
    services = _tree(tmp_path, {"resource_vector.py": "import agent_friday.services.issuer_journal\n"})
    done = subprocess.run([sys.executable, str(SCRIPT), str(services)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 1
    assert done.stdout.strip().splitlines() == ["resource_vector.py: imports issuer_journal"]


def test_ci_runs_the_script():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "python scripts/check_resource_money_imports.py" in workflow
